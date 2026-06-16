# Atlas MCP Server

Model Context Protocol server providing AI access to Apache Atlas data catalog via Apache Knox.

## Features

- **Knox authentication** — supports JWT tokens, raw cookies, and Basic Auth for CDP deployments
- **Read-mostly** — safe exploration of entities, lineage, types, and glossaries; a small set of write tools for tagging and labeling
- **Automatic retries** — exponential backoff on transient errors

### MCP Tools

**Admin / Status**

- `get_atlas_status()` — Server health information
- `get_atlas_metrics()` — Entity and tag counts by type
- `get_atlas_version()` — Atlas version

**Search**

- `search_entities(query, type_name?, classification?, limit, offset, exclude_deleted)` — Basic search; use `'*'` to browse all
- `fulltext_search(query, limit, offset, exclude_deleted)` — Full-text search across attribute values and descriptions
- `dsl_search(query, limit, offset)` — Atlas DSL queries (e.g. `hive_table where db.name="default"`)
- `search_by_classification(classification, entity_type?, limit, offset)` — Find all entities with a given tag (e.g. `PII`)

**Entity**

- `get_entity(guid, ignore_relationships?)` — Full entity details including classifications and relationships
- `get_entity_by_attribute(type_name, attr_name, attr_value)` — Lookup by `qualifiedName` or other unique attribute
- `get_entity_classifications(guid)` — Tags applied to an entity
- `get_entity_labels(guid)` — Free-form labels on an entity
- `get_entity_audit(guid, count)` — Audit history of attribute and classification changes
- `get_entities_bulk(guids)` — Fetch multiple entities in one call (comma-separated GUIDs)

**Lineage**

- `get_lineage(guid, direction, depth)` — Data lineage graph (`INPUT`, `OUTPUT`, or `BOTH`)
- `get_lineage_by_attribute(type_name, attr_name, attr_value, direction, depth)` — Lineage without a GUID

**Types**

- `list_entity_types()` — All registered entity type names
- `list_classification_types()` — All classification (tag) type names
- `get_entity_type_definition(type_name)` — Full attribute schema for an entity type
- `get_classification_definition(classification_name)` — Attribute schema for a classification type

**Glossary**

- `list_glossaries()` — All business glossaries
- `list_glossary_terms(glossary_guid?, limit, offset)` — Terms in a glossary (or all terms)
- `get_glossary_term(term_guid)` — Term definition and linked entities
- `get_entities_for_glossary_term(term_guid, limit, offset)` — Data assets linked to a term

**Relationship**

- `get_relationship(guid)` — Details of an entity-to-entity relationship

**Write operations**

- `add_classification_to_entity(guid, classification_name, attributes?)` — Apply a tag to an entity
- `remove_classification_from_entity(guid, classification_name)` — Remove a tag from an entity
- `add_labels_to_entity(guid, labels)` — Add free-form labels (comma-separated)

## Setup

### Option 1: Claude Desktop (Local)

1. **Clone and install:**
  ```bash
   git clone <repo-url>
   cd Atlas-MCP-Server
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
  ```
2. **Configure Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
  ```json
   {
     "mcpServers": {
       "atlas-mcp-server": {
         "command": "/FULL/PATH/TO/Atlas-MCP-Server/.venv/bin/python",
         "args": ["-m", "atlas_mcp_server.server"],
         "env": {
           "ATLAS_GATEWAY_URL": "https://<host>/<topology>/cdp-proxy-api/atlas/api/atlas/",
           "KNOX_TOKEN": "<your_knox_jwt_token>"
         }
       }
     }
   }
  ```
3. **Restart Claude Desktop** and start asking questions about your data catalog.

### Option 2: uvx (Cloudera Agent Studio)

```json
{
  "mcpServers": {
    "atlas-mcp-server": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ecole5/atlas-mcp@main",
        "run-server"
      ],
      "env": {
        "ATLAS_GATEWAY_URL": "https://<host>/<topology>/cdp-proxy-api/atlas/api/atlas/",
        "KNOX_TOKEN": "<your_knox_jwt_token>"
      }
    }
  }
}
```

## Configuration

All configuration is done via environment variables.

### Connection


| Variable            | Required | Description                                                                                    |
| ------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `ATLAS_GATEWAY_URL` | Yes      | Full CDP Knox Atlas API URL (e.g. `https://<host>/<topology>/cdp-proxy-api/atlas/api/atlas/`). |


### Authentication (pick one)


| Variable                    | Priority | Description                                   |
| --------------------------- | -------- | --------------------------------------------- |
| `KNOX_COOKIE`               | Highest  | Raw cookie string (e.g. `hadoop-jwt=<token>`) |
| `KNOX_TOKEN`                | Medium   | Knox JWT token — sent as `hadoop-jwt` cookie  |
| `ATLAS_USER` + `ATLAS_PASS` | Lowest   | Basic auth credentials                        |


### TLS / HTTP


| Variable               | Default | Description                                     |
| ---------------------- | ------- | ----------------------------------------------- |
| `ATLAS_VERIFY_SSL`     | `true`  | Set `false` to disable certificate verification |
| `ATLAS_CA_BUNDLE`      | —       | Path to a CA certificate bundle                 |
| `HTTP_TIMEOUT_SECONDS` | `30`    | Request timeout in seconds                      |
| `HTTP_MAX_RETRIES`     | `3`     | Maximum retry attempts on transient errors      |


### CDP URL pattern

The `ATLAS_GATEWAY_URL` for a CDP Flow Management DataHub is the full Atlas API path through Knox, for example:

```
https://<cluster-host>/<topology>/cdp-proxy-api/atlas/api/atlas/
```

A trailing slash is optional.

## Example Queries

Once configured, you can ask Claude things like:

- "What entity types are registered in Atlas?"
- "Find all Hive tables in the default database"
- "Show me the lineage for table `default.orders@mycluster`"
- "Which entities are tagged as PII?"
- "What does the glossary term 'Customer ID' mean?"
- "Show me the audit history for entity guid abc-123"
- "Search for all Kafka topics containing 'events'"
- "Tag entity abc-123 as Confidential"

## License

Apache License 2.0