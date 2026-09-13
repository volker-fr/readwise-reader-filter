> **WARNING: Experimental software.**
>
> This tool is experimental and not well tested. Filter rules may
> match unexpected entries. Items could be accidentally deleted,
> archived, or otherwise modified without recovery.
>
> **Use at your own risk.** Always run `--dry-run` first to review
> what would happen before executing.

---

# readwise-reader-filter

Filter and manage Readwise Reader (read.readwise.io) feed entries by configurable rules.
Not the same as Readwise (readwise.io). Only the "feed" location is supported.

```
readwise-reader-filter --dry-run --debug
```

## Installation

```bash
# With uv (recommended)
uv pip install .

# Or with pip
pip install .
```

## Features

- **Global rules** apply to every feed entry.
- **Feed-specific rules** apply only to entries matching a feed definition
  (by `authors`, `site_names`, `domains`, `categories`).
- **Same filter primitives** work identically in both scopes.
- **`--dry-run`** shows what would happen without executing.
- **`--debug`** shows detailed per-step reasoning for matched entries.
- **`--validate`** checks config file for syntax errors, typos, and invalid
  patterns before making any API calls.
- **Caching** avoids re-downloading all documents on every run.
- **Action history** logs executed actions to `~/.cache/readwise-reader-filter/actions.jsonl`.

## Actions

| Action            | What it does                                             |
|-------------------|----------------------------------------------------------|
| `delete`          | Hard-deletes the entry from Readwise Reader.             |
| `mark_for_delete` | Soft-delete: adds `"delete"` tag, moves to `later`.     |
| `archive`         | Moves the entry to the Archive location.                 |
| `seen`            | Marks entry as seen (sets `first_opened_at`).            |
| `unseen`          | Marks entry as unseen (clears `first_opened_at`).        |

## Filter fields

| Field          | Sub-filters                                               |
|----------------|-----------------------------------------------------------|
| `title`        | `contains`, `contains_any`, `matches` (regex), `exclude_with` |
| `summary`      | same as title (matches against summary text)             |
| `content`      | same as title (matches against full article HTML body)   |
| `url`          | same as title (matches against `source_url`)             |
| `tags_include` | entry must have ALL of these tags                        |
| `tags_exclude` | entry must have NONE of these tags                       |
| `age`          | `older_than_days` — entry must be older than N days      |
| `read`         | `true` = match seen entries, `false` = match unseen entries |

Feed `match` fields (`site_names`, `authors`, `categories`) accept either a
plain list (exact, case-insensitive match against any value) or the same
filter dict as above for substring/regex matching, e.g.
`authors: { contains: ["Smith"] }` or `authors: { matches: ["^J\\. R\\.$"] }`.
`domains` keeps its exact/subdomain matching (`"youtube.com"` also matches
`"www.youtube.com"`).

## Action resolution

If an entry matches multiple rules (e.g. both a global rule and a
feed-specific rule), the most destructive action wins:

1. `delete` (highest precedence)
2. `mark_for_delete`
3. `archive`
4. `seen` / `unseen` (lowest precedence)

When two rules have the same action, a **global** rule is preferred
over a feed-specific rule in the displayed result.

## Config structure

```yaml
global:
  - name: "Block sponsored content"
    title:
      matches:
        - "^\\[sponsored\\]"
    action: delete

feeds:
  - name: "YouTube"
    match:
      domains: ["youtube.com"]
    rules:
      - name: "Block shorts"
        url:
          contains: ["/shorts/"]
        action: delete

      - name: "Archive old videos"
        age:
          older_than_days: 30
        action: archive
```

## Usage

```bash
# List what would be done (safe)
readwise-reader-filter --dry-run

# Same with detailed reasoning
readwise-reader-filter --dry-run --debug

# Actually execute actions
readwise-reader-filter

# Force re-download from API
readwise-reader-filter --refresh

# Custom config path
readwise-reader-filter --config my-rules.yaml

# Validate config file without making API calls
readwise-reader-filter --validate
```

## Configuration

### API token

Set the `READWISE_API_TOKEN` environment variable with your Readwise API token:

```bash
export READWISE_API_TOKEN="your_token_here"
```

Get your token from [Readwise settings](https://readwise.io/access_token).

### Config file

Default config path: `~/.config/readwise-reader-filter/config.yaml`
Override via `--config` flag or `READWISE_FILTER_CONFIG` env var.

Example config: [`example-config.yaml`](example-config.yaml) in the repository root.

### Config validation

The validator checks config files for:

| Check | Severity | Description |
|-------|----------|-------------|
| Unknown keys | Error | Typos like `titlee` or `badsub` in any dict |
| Invalid action | Error | Actions must be `delete`, `mark_for_delete`, `archive`, `seen`, or `unseen` |
| Invalid regex | Error | `matches` patterns are compiled at config load time |
| Bad field types | Error | `title` must be string/dict, `age` must be dict, `read` must be bool |
| Missing `match` type | Error | Feed `match` must be a dict if provided |
| Rule with no filters | Warning | Matches everything — likely a mistake |
| Empty rules list | Warning | Feed matches but does nothing |
| `mark_all_as_unseen` + seen/unseen rules | Warning | Bulk operation will override individual rules |

Run with `--validate` to check without making API calls.

### mark_all_as_unseen

Set `mark_all_as_unseen: true` at the top level to mark all feed entries
as unseen in bulk. This is useful for resetting read state.

```yaml
mark_all_as_unseen: true
```

## Development

```bash
# Install dependencies
make install

# Run tests
make test

# Lint
make lint

# Format
make format
```

## Notes

- This tool is for **Readwise Reader** (read.readwise.io), not Readwise (readwise.io).
- Only the `feed` location is supported — entries in archive, later, inbox, etc. are NOT affected.

## Future improvements

- Utilize `updatedAfter` parameter to avoid re-fetching unchanged documents.
