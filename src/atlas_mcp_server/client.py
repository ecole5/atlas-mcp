from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

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


class AtlasClient:
    """HTTP client for Atlas v2 REST API (CDP 7.3.1 compatible)."""

    def __init__(self, base_url: str, session: requests.Session, timeout_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout = timeout_seconds

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _unique_attr_params(attr_name: str, attr_value: str) -> Dict[str, str]:
        return {f"attr:{attr_name}": attr_value}

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
        return self._get("admin/status")

    def get_metrics(self) -> Dict[str, Any]:
        return self._get("admin/metrics")

    def get_version(self) -> Dict[str, Any]:
        return self._get("admin/version")

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
