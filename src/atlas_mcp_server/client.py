## Copyright (c) 2025 Cloudera, Inc. All Rights Reserved.
##
## This file is licensed under the Apache License Version 2.0 (the "License").
## You may not use this file except in compliance with the License.
## You may obtain a copy of the License at http:##www.apache.org/licenses/LICENSE-2.0.
##
## This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
## OF ANY KIND, either express or implied. Refer to the License for the specific
## permissions and limitations governing your use of the file.

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class AtlasError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)

    def __str__(self) -> str:
        msg = super().__str__()
        if self.status_code:
            msg = f"[{self.status_code}] {msg}"
        if self.response_body:
            msg = f"{msg}\n\nAtlas API Response:\n{self.response_body}"
        return msg


_RETRYABLE = (AtlasError, requests.ConnectionError, requests.Timeout)


def _bool_query(value: bool) -> str:
    return str(value).lower()


def _entity_body(entity_response: Dict[str, Any]) -> Dict[str, Any]:
    body = entity_response.get("entity")
    return body if isinstance(body, dict) else entity_response


TABLE_TYPES_WITH_COLUMNS = frozenset({"hive_table", "iceberg_table"})


def _is_table_asset(entity_body: Dict[str, Any]) -> bool:
    return entity_body.get("typeName") in TABLE_TYPES_WITH_COLUMNS


def _extract_table_columns(
    entity_response: Dict[str, Any], column_limit: int
) -> Dict[str, Any]:
    entity_body = _entity_body(entity_response)
    refs = (entity_body.get("relationshipAttributes") or {}).get("columns") or []
    referred = entity_response.get("referredEntities") or {}

    columns: List[Dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        col_guid = ref.get("guid")
        full_col = referred.get(col_guid, ref) if col_guid else ref
        if isinstance(full_col, dict) and "entity" in full_col:
            full_col = full_col["entity"]
        attrs = full_col.get("attributes") if isinstance(full_col, dict) else None
        attrs = attrs or {}

        columns.append(
            {
                "guid": col_guid or (full_col.get("guid") if isinstance(full_col, dict) else None),
                "name": attrs.get("name") or ref.get("displayText"),
                "dataType": attrs.get("type") or attrs.get("dataType"),
                "qualifiedName": attrs.get("qualifiedName") or ref.get("qualifiedName"),
                "position": attrs.get("position"),
                "comment": attrs.get("comment") or attrs.get("description"),
            }
        )

    columns.sort(
        key=lambda col: (
            col["position"] is None,
            col["position"] if col["position"] is not None else 0,
            col["name"] or "",
        )
    )

    total = len(columns)
    result: Dict[str, Any] = {
        "total_columns": total,
        "columns": columns[:column_limit],
    }
    if total > column_limit:
        result["truncated"] = True
        result["column_limit"] = column_limit
    return result


def _lineage_neighbor_summary(guid: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    attrs = entry.get("attributes") or {}
    return {
        "guid": guid,
        "typeName": entry.get("typeName"),
        "name": attrs.get("name") or entry.get("displayText"),
        "qualifiedName": attrs.get("qualifiedName"),
        "displayText": entry.get("displayText"),
    }


def _summarize_lineage(lineage: Dict[str, Any], base_guid: str) -> Dict[str, List[Dict[str, Any]]]:
    entity_map = lineage.get("guidEntityMap") or {}
    relations = lineage.get("relations") or []
    upstream: List[Dict[str, Any]] = []
    downstream: List[Dict[str, Any]] = []
    seen_upstream: set[str] = set()
    seen_downstream: set[str] = set()

    for relation in relations:
        from_id = relation.get("fromEntityId")
        to_id = relation.get("toEntityId")
        if to_id == base_guid and from_id and from_id not in seen_upstream:
            seen_upstream.add(from_id)
            upstream.append(_lineage_neighbor_summary(from_id, entity_map.get(from_id, {})))
        if from_id == base_guid and to_id and to_id not in seen_downstream:
            seen_downstream.add(to_id)
            downstream.append(_lineage_neighbor_summary(to_id, entity_map.get(to_id, {})))

    return {"upstream": upstream, "downstream": downstream}


def _extract_glossary_terms(entity_body: Dict[str, Any]) -> List[Dict[str, Any]]:
    terms: List[Dict[str, Any]] = []
    for meaning in entity_body.get("meanings") or []:
        if isinstance(meaning, dict):
            terms.append(
                {
                    "termGuid": meaning.get("termGuid") or meaning.get("guid"),
                    "displayText": meaning.get("displayText") or meaning.get("name"),
                    "confidence": meaning.get("confidence"),
                }
            )
    if not terms:
        for name in entity_body.get("meaningNames") or []:
            terms.append({"displayText": name})
    return terms


def _normalize_classifications(raw: Any, entity_body: Dict[str, Any]) -> List[Any]:
    if isinstance(raw, dict):
        items = raw.get("list") or raw.get("classifications")
        if items is not None:
            return list(items)
    if isinstance(raw, list):
        return raw
    inline = entity_body.get("classifications")
    if inline:
        return list(inline)
    return [{"typeName": name} for name in entity_body.get("classificationNames") or []]


def _fetch_lineage_summary(
    fetch_lineage: Any,
    guid: str,
    lineage_depth: int,
) -> Dict[str, Any]:
    try:
        lineage = fetch_lineage(guid, direction="BOTH", depth=lineage_depth)
        return {
            "depth": lineage_depth,
            "supported": True,
            **_summarize_lineage(lineage, guid),
        }
    except AtlasError as exc:
        if _is_lineage_unsupported(exc):
            return {
                "depth": lineage_depth,
                "supported": False,
                "upstream": [],
                "downstream": [],
                "note": "Lineage is not available for this entity type.",
            }
        raise


def _is_lineage_unsupported(exc: AtlasError) -> bool:
    body = (exc.response_body or "").lower()
    return (
        exc.status_code == 404
        or "lineage" in body
        or "not a valid lineage entity type" in body
    )


def _impact_display_name(entry: Dict[str, Any], guid: str) -> str:
    attrs = entry.get("attributes") or {}
    return (
        attrs.get("qualifiedName")
        or attrs.get("name")
        or entry.get("displayText")
        or guid
    )


def _compute_downstream_impacts(
    lineage: Dict[str, Any],
    base_guid: str,
    max_depth: int,
    *,
    exact_depth: bool = False,
    entity_types: Optional[set[str]] = None,
    max_impacts: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Return downstream impacts with shortest-hop BFS.

    Traversal always walks through all entity types; entity_types filters what is returned.
    When exact_depth is True, only entities whose shortest hop equals max_depth are returned.
    """
    entity_map = lineage.get("guidEntityMap") or {}
    relations = lineage.get("relations") or []
    adjacency: Dict[str, List[str]] = {}
    for relation in relations:
        from_id = relation.get("fromEntityId")
        to_id = relation.get("toEntityId")
        if from_id and to_id:
            adjacency.setdefault(from_id, []).append(to_id)

    hop_map: Dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque([(base_guid, 0)])

    while queue:
        current_guid, hop = queue.popleft()
        if hop >= max_depth:
            continue
        for neighbor_guid in adjacency.get(current_guid, []):
            if neighbor_guid == base_guid:
                continue
            next_hop = hop + 1
            if next_hop > max_depth:
                continue
            if neighbor_guid not in hop_map:
                hop_map[neighbor_guid] = next_hop
                queue.append((neighbor_guid, next_hop))

    results: List[Dict[str, Any]] = []
    truncated = False
    for neighbor_guid, hop in sorted(hop_map.items(), key=lambda item: (item[1], item[0])):
        if exact_depth and hop != max_depth:
            continue
        entry = entity_map.get(neighbor_guid, {})
        entry_type = entry.get("typeName")
        if entity_types and entry_type not in entity_types:
            continue
        results.append(
            {
                "guid": neighbor_guid,
                "type": entry_type,
                "name": _impact_display_name(entry, neighbor_guid),
                "hop": hop,
            }
        )
        if max_impacts is not None and len(results) >= max_impacts:
            truncated = True
            break
    return results, truncated


def _source_summary(entity_body: Dict[str, Any], guid: str) -> Dict[str, Any]:
    attrs = entity_body.get("attributes") or {}
    return {
        "guid": guid,
        "type": entity_body.get("typeName"),
        "name": attrs.get("name") or attrs.get("qualifiedName"),
        "qualifiedName": attrs.get("qualifiedName"),
    }


class AtlasClient:
    """HTTP client for Atlas v2 REST API (CDP 7.3.1 compatible)."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session,
        timeout_seconds: int = 30,
        api_root_url: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_root_url = (api_root_url or base_url).rstrip("/")
        self.session = session
        self.timeout = timeout_seconds

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _api_root_url(self, path: str) -> str:
        return f"{self.api_root_url}/{path.lstrip('/')}"

    @staticmethod
    def _unique_attr_params(attr_name: str, attr_value: str) -> Dict[str, str]:
        return {f"attr:{attr_name}": attr_value}

    @contextmanager
    def _use_timeout(self, timeout_seconds: Optional[int]) -> Iterator[None]:
        if timeout_seconds is None:
            yield
            return
        previous = self.timeout
        self.timeout = timeout_seconds
        try:
            yield
        finally:
            self.timeout = previous

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, path: str, params: Optional[Union[Dict[str, Any], List[tuple[str, Any]]]] = None) -> Any:
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        if not resp.ok:
            raise AtlasError(f"GET {path} failed: {resp.reason}", resp.status_code, resp.text or "(empty)")
        return resp.json()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get_api_root(
        self, path: str, params: Optional[Union[Dict[str, Any], List[tuple[str, Any]]]] = None
    ) -> Any:
        resp = self.session.get(self._api_root_url(path), params=params, timeout=self.timeout)
        if not resp.ok:
            raise AtlasError(f"GET {path} failed: {resp.reason}", resp.status_code, resp.text or "(empty)")
        return resp.json()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _post(self, path: str, data: Any, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self.session.post(self._url(path), json=data, params=params, timeout=self.timeout)
        if not resp.ok:
            raise AtlasError(f"POST {path} failed: {resp.reason}", resp.status_code, resp.text or "(empty)")
        return resp.json() if resp.content else {}

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _put(self, path: str, data: Any = None, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self.session.put(self._url(path), json=data, params=params, timeout=self.timeout)
        if not resp.ok:
            raise AtlasError(f"PUT {path} failed: {resp.reason}", resp.status_code, resp.text or "(empty)")
        return resp.json() if resp.content else {}

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _delete(self, path: str, params: Optional[Dict[str, Any]] = None, data: Any = None) -> Any:
        resp = self.session.delete(self._url(path), params=params, json=data, timeout=self.timeout)
        if not resp.ok:
            raise AtlasError(f"DELETE {path} failed: {resp.reason}", resp.status_code, resp.text or "(empty)")
        return resp.json() if resp.content else {}

    # ── Admin ──────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        return self._get_api_root("admin/status")

    def get_metrics(self) -> Dict[str, Any]:
        return self._get_api_root("admin/metrics")

    def get_version(self) -> Dict[str, Any]:
        return self._get_api_root("admin/version")

    # ── Search (DiscoveryREST) ─────────────────────────────────────────────

    def search_basic(
        self,
        query: str = "*",
        type_name: Optional[str] = None,
        classification: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
        exclude_deleted: bool = True,
        marker: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/search/basic — searchUsingBasic."""
        params: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "offset": offset,
            "excludeDeletedEntities": _bool_query(exclude_deleted),
        }
        if type_name is not None:
            params["typeName"] = type_name
        if classification is not None:
            params["classification"] = classification
        if marker is not None:
            params["marker"] = marker
        if sort_by is not None:
            params["sortBy"] = sort_by
        if sort_order is not None:
            params["sortOrder"] = sort_order
        return self._get("search/basic", params=params)

    def search_fulltext(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
        exclude_deleted: bool = True,
    ) -> Dict[str, Any]:
        """GET /v2/search/fulltext — searchUsingFullText."""
        return self._get(
            "search/fulltext",
            params={
                "query": query,
                "limit": limit,
                "offset": offset,
                "excludeDeletedEntities": _bool_query(exclude_deleted),
            },
        )

    def search_dsl(
        self,
        query: Optional[str] = None,
        type_name: Optional[str] = None,
        classification: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """GET /v2/search/dsl — searchUsingDSL."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query is not None:
            params["query"] = query
        if type_name is not None:
            params["typeName"] = type_name
        if classification is not None:
            params["classification"] = classification
        return self._get("search/dsl", params=params)

    def search_by_classification(
        self,
        classification: str,
        entity_type: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
        exclude_deleted: bool = True,
    ) -> Dict[str, Any]:
        return self.search_basic(
            query="*",
            type_name=entity_type,
            classification=classification,
            limit=limit,
            offset=offset,
            exclude_deleted=exclude_deleted,
        )

    def search_saved(self) -> Dict[str, Any]:
        return self._get("search/saved")

    # ── Entity (EntityREST) ────────────────────────────────────────────────

    def get_entity_by_guid(
        self,
        guid: str,
        min_ext_info: bool = False,
        ignore_relationships: bool = False,
    ) -> Dict[str, Any]:
        """GET /v2/entity/guid/{guid} — getById."""
        return self._get(
            f"entity/guid/{guid}",
            params={
                "minExtInfo": _bool_query(min_ext_info),
                "ignoreRelationships": _bool_query(ignore_relationships),
            },
        )

    def get_entity_by_attribute(
        self,
        type_name: str,
        attr_name: str,
        attr_value: str,
        min_ext_info: bool = False,
        ignore_relationships: bool = False,
    ) -> Dict[str, Any]:
        """GET /v2/entity/uniqueAttribute/type/{typeName} — getByUniqueAttributes."""
        params: Dict[str, Any] = {
            **self._unique_attr_params(attr_name, attr_value),
            "minExtInfo": _bool_query(min_ext_info),
            "ignoreRelationships": _bool_query(ignore_relationships),
        }
        return self._get(f"entity/uniqueAttribute/type/{type_name}", params=params)

    def get_entities_by_guids(
        self,
        guids: List[str],
        min_ext_info: bool = False,
        ignore_relationships: bool = False,
    ) -> Dict[str, Any]:
        """GET /v2/entity/bulk — getByGuids."""
        params: List[tuple[str, Any]] = [
            ("guid", guid) for guid in guids
        ] + [
            ("minExtInfo", _bool_query(min_ext_info)),
            ("ignoreRelationships", _bool_query(ignore_relationships)),
        ]
        return self._get("entity/bulk", params=params)

    def get_entity_classifications(self, guid: str) -> Dict[str, Any]:
        """GET /v2/entity/guid/{guid}/classifications — getClassifications."""
        return self._get(f"entity/guid/{guid}/classifications")

    def get_entity_labels(self, guid: str) -> Dict[str, Any]:
        """Labels are returned on the entity; no dedicated GET in CDP 7.3.1 API ref."""
        entity = self.get_entity_by_guid(guid, ignore_relationships=True)
        labels: List[str] = []
        if isinstance(entity, dict):
            entity_body = entity.get("entity", entity)
            if isinstance(entity_body, dict):
                raw_labels = entity_body.get("labels") or []
                labels = list(raw_labels)
        return {"labels": labels}

    def _resolve_entity(
        self,
        guid: Optional[str] = None,
        type_name: Optional[str] = None,
        attr_name: Optional[str] = None,
        attr_value: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        if not guid:
            resolved_attr_name = attr_name or "qualifiedName"
            if not type_name or not attr_value:
                raise ValueError(
                    "Provide guid, or type_name with attr_value (and optionally attr_name)."
                )
            entity_response = self.get_entity_by_attribute(
                type_name,
                resolved_attr_name,
                attr_value,
                ignore_relationships=True,
            )
            entity_body = _entity_body(entity_response)
            guid = entity_body.get("guid")
            if not guid:
                raise AtlasError("Entity lookup succeeded but no GUID was returned.")
            return guid, entity_body

        entity_response = self.get_entity_by_guid(guid, ignore_relationships=True)
        return guid, _entity_body(entity_response)

    def describe_asset(
        self,
        guid: Optional[str] = None,
        type_name: Optional[str] = None,
        attr_name: Optional[str] = None,
        attr_value: Optional[str] = None,
        lineage_depth: int = 1,
        include_columns: bool = False,
        column_limit: int = 100,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a consolidated view of an asset: entity, tags, labels, terms, and lineage."""
        if column_limit < 1:
            raise ValueError("column_limit must be at least 1.")
        if timeout_seconds is not None and timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1.")

        with self._use_timeout(timeout_seconds):
            guid, entity_body = self._resolve_entity(guid, type_name, attr_name, attr_value)
            if include_columns and _is_table_asset(entity_body):
                entity_response = self.get_entity_by_guid(guid, ignore_relationships=False)
                entity_body = _entity_body(entity_response)
            else:
                entity_response = None

            attrs = entity_body.get("attributes") or {}
            classifications_raw = self.get_entity_classifications(guid)
            lineage_summary = _fetch_lineage_summary(self.get_lineage_by_guid, guid, lineage_depth)

            result: Dict[str, Any] = {
                "summary": {
                    "guid": guid,
                    "typeName": entity_body.get("typeName"),
                    "status": entity_body.get("status"),
                    "name": attrs.get("name"),
                    "qualifiedName": attrs.get("qualifiedName"),
                    "displayText": attrs.get("qualifiedName") or attrs.get("name"),
                    "createdBy": entity_body.get("createdBy"),
                    "updatedBy": entity_body.get("updatedBy"),
                    "createTime": entity_body.get("createTime") or attrs.get("createTime"),
                    "updateTime": entity_body.get("updateTime"),
                },
                "attributes": attrs,
                "classifications": _normalize_classifications(classifications_raw, entity_body),
                "labels": list(entity_body.get("labels") or []),
                "glossary_terms": _extract_glossary_terms(entity_body),
                "lineage": lineage_summary,
            }
            if include_columns and _is_table_asset(entity_body) and entity_response is not None:
                result["columns"] = _extract_table_columns(entity_response, column_limit)
            return result

    def impact_analysis(
        self,
        guid: Optional[str] = None,
        type_name: Optional[str] = None,
        attr_name: Optional[str] = None,
        attr_value: Optional[str] = None,
        depth: int = 1,
        exact_depth: bool = False,
        entity_types: Optional[List[str]] = None,
        max_impacts: Optional[int] = 100,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Downstream impact analysis: what depends on this asset within N hops."""
        if depth < 1:
            raise ValueError("depth must be at least 1.")
        if timeout_seconds is not None and timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1.")

        with self._use_timeout(timeout_seconds):
            guid, entity_body = self._resolve_entity(guid, type_name, attr_name, attr_value)
            type_filter = set(entity_types) if entity_types else None

            try:
                lineage = self.get_lineage_by_guid(guid, direction="OUTPUT", depth=depth)
                impacts, truncated = _compute_downstream_impacts(
                    lineage,
                    guid,
                    depth,
                    exact_depth=exact_depth,
                    entity_types=type_filter,
                    max_impacts=max_impacts,
                )
                response: Dict[str, Any] = {
                    "source": _source_summary(entity_body, guid),
                    "depth": depth,
                    "exact_depth": exact_depth,
                    "entity_types": sorted(type_filter) if type_filter else None,
                    "supported": True,
                    "total_impacts": len(impacts),
                    "impacts": impacts,
                }
                if truncated:
                    response["truncated"] = True
                    response["max_impacts"] = max_impacts
                if type_filter:
                    response["note"] = (
                        "Intermediate entity types were traversed but not returned. "
                        "Use exact_depth=true with a higher depth to explore hop-by-hop."
                    )
                return response
            except AtlasError as exc:
                if _is_lineage_unsupported(exc):
                    return {
                        "source": _source_summary(entity_body, guid),
                        "depth": depth,
                        "exact_depth": exact_depth,
                        "entity_types": sorted(type_filter) if type_filter else None,
                        "supported": False,
                        "total_impacts": 0,
                        "impacts": [],
                        "note": "Lineage is not available for this entity type.",
                    }
                raise

    def get_entity_audit(
        self,
        guid: str,
        count: int = 100,
        offset: int = -1,
        start_key: Optional[str] = None,
        audit_action: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """GET /v2/entity/{guid}/audit — getAuditEvents."""
        params: Dict[str, Any] = {"count": count, "offset": offset}
        if start_key is not None:
            params["startKey"] = start_key
        if audit_action is not None:
            params["auditAction"] = audit_action
        if sort_by is not None:
            params["sortBy"] = sort_by
        if sort_order is not None:
            params["sortOrder"] = sort_order
        return self._get(f"entity/{guid}/audit", params=params)

    def get_entity_header(self, guid: str) -> Dict[str, Any]:
        """GET /v2/entity/guid/{guid}/header."""
        return self._get(f"entity/guid/{guid}/header")

    def add_classification(self, guid: str, classifications: List[Dict[str, Any]]) -> None:
        """POST /v2/entity/guid/{guid}/classifications — addClassifications."""
        self._post(f"entity/guid/{guid}/classifications", classifications)

    def remove_classification(self, guid: str, classification_name: str) -> None:
        """DELETE /v2/entity/guid/{guid}/classification/{classificationName}."""
        self._delete(f"entity/guid/{guid}/classification/{classification_name}")

    def add_labels(self, guid: str, labels: List[str]) -> None:
        """PUT /v2/entity/guid/{guid}/labels — addLabels."""
        self._put(f"entity/guid/{guid}/labels", labels)

    def remove_labels(self, guid: str, labels: List[str]) -> None:
        """DELETE /v2/entity/guid/{guid}/labels — removeLabels."""
        self._delete(f"entity/guid/{guid}/labels", data=labels)

    def update_entity_attribute(
        self, guid: str, attr_name: str, attr_value: Any
    ) -> Dict[str, Any]:
        """PUT /v2/entity/guid/{guid}?name= — partialUpdateEntityAttrByGuid."""
        return self._put(
            f"entity/guid/{guid}",
            data=attr_value,
            params={"name": attr_name},
        )

    # ── Lineage (LineageREST) ──────────────────────────────────────────────

    def get_lineage_by_guid(
        self,
        guid: str,
        direction: str = "BOTH",
        depth: int = 3,
    ) -> Dict[str, Any]:
        """GET /v2/lineage/{guid} — getLineageGraph."""
        return self._get(
            f"lineage/{guid}",
            params={"direction": direction, "depth": depth},
        )

    def get_lineage_by_attribute(
        self,
        type_name: str,
        attr_name: str,
        attr_value: str,
        direction: str = "BOTH",
        depth: int = 3,
    ) -> Dict[str, Any]:
        """GET /v2/lineage/uniqueAttribute/type/{typeName} — getLineageByUniqueAttribute."""
        params: Dict[str, Any] = {
            **self._unique_attr_params(attr_name, attr_value),
            "direction": direction,
            "depth": depth,
        }
        return self._get(f"lineage/uniqueAttribute/type/{type_name}", params=params)

    # ── Types (TypesREST) ──────────────────────────────────────────────────

    def get_all_type_defs(self) -> Dict[str, Any]:
        return self._get("types/typedefs")

    def get_entity_type_def(self, type_name: str) -> Dict[str, Any]:
        """GET /v2/types/entitydef/name/{name} — getEntityDefByName."""
        return self._get(f"types/entitydef/name/{type_name}")

    def get_classification_type_def(self, type_name: str) -> Dict[str, Any]:
        """GET /v2/types/classificationdef/name/{name} — getClassificationDefByName."""
        return self._get(f"types/classificationdef/name/{type_name}")

    def get_type_def_by_name(self, type_name: str) -> Dict[str, Any]:
        return self._get(f"types/typedef/name/{type_name}")

    def list_type_names(self, type_category: Optional[str] = None) -> Any:
        """GET /v2/types/typedefs/headers — getTypeDefHeaders.

        CDP 7.3.1 does not support server-side category filtering; filter client-side.
        """
        headers = self._get("types/typedefs/headers")
        if not type_category:
            return headers
        if isinstance(headers, list):
            return [header for header in headers if header.get("category") == type_category]
        return headers

    # ── Glossary (GlossaryREST) ────────────────────────────────────────────

    def list_glossaries(
        self, limit: int = 100, offset: int = 0, sort: str = "ASC"
    ) -> List[Dict[str, Any]]:
        """GET /v2/glossary — getGlossaries."""
        return self._get("glossary", params={"limit": limit, "offset": offset, "sort": sort})

    def get_glossary(self, glossary_guid: str) -> Dict[str, Any]:
        return self._get(f"glossary/{glossary_guid}")

    def list_glossary_terms(
        self,
        glossary_guid: str,
        limit: int = 100,
        offset: int = 0,
        sort: str = "ASC",
    ) -> Any:
        """GET /v2/glossary/{glossaryGuid}/terms — getGlossaryTerms."""
        return self._get(
            f"glossary/{glossary_guid}/terms",
            params={"limit": limit, "offset": offset, "sort": sort},
        )

    def get_glossary_term(self, term_guid: str) -> Dict[str, Any]:
        return self._get(f"glossary/term/{term_guid}")

    def get_entities_for_term(
        self, term_guid: str, limit: int = 25, offset: int = 0, sort: str = "ASC"
    ) -> Any:
        """GET /v2/glossary/terms/{termGuid}/assignedEntities."""
        return self._get(
            f"glossary/terms/{term_guid}/assignedEntities",
            params={"limit": limit, "offset": offset, "sort": sort},
        )

    # ── Relationships (RelationshipREST) ───────────────────────────────────

    def get_relationship_by_guid(
        self, guid: str, extended_info: bool = False
    ) -> Dict[str, Any]:
        """GET /v2/relationship/guid/{guid} — getById."""
        return self._get(
            f"relationship/guid/{guid}",
            params={"extendedInfo": _bool_query(extended_info)},
        )
