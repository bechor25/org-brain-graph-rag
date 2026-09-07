# חיבור מערכת חדשה למוח הארגוני

**קהל:** מי שרוצה לחבר Jira / Confluence / Azure DevOps / Xray / git אמיתיים של הארגון במקום קורפוס ה-POC.
**החלטה:** [ADR-0005](../decisions/0005-modularity-and-reset.md). **קוד:** `brain/harvest/`, `brain/canon/mappers/`, `sources.yaml`.

הגבול של המודול הוא **המודל הקנוני** (`brain/canon/models.py`). כל מה שאחרי `brain canon` — טעינה לגרף, chunking, חילוץ, entity resolution, קהילות, MCP — לא יודע ולא צריך לדעת מאיפה הדאטה הגיע. לכן מערכת חדשה היא **שלושה קבצים ורשומה אחת בקונפיג**, ואף פעם לא סכמה חדשה.

---

## 1. מה צריך לכתוב

| # | קובץ | תפקיד |
|---|---|---|
| 1 | `brain/harvest/<source>.py` | קונקטור: `probe()`, `fetch()`, checkpoint אחרי כל דף. כותב raw גולמי ל-`data/raw/<name>/` ולא מנרמל כלום. |
| 2 | `brain/canon/mappers/<source>.py` | mapper: raw → חמשת הטיפוסים הקנוניים (`WorkItem`, `Document`, `Person`, `Change`, `Container`). |
| 3 | `tests/test_canon_<source>.py` | בדיקת golden: תשובה גולמית אמיתית אחת מול רשומה קנונית קפואה. |
| 4 | `sources.yaml` | רשומה אחת: איפה, מה השאילתה, אילו מפתחות פרויקט, איזה משתנה סביבה. |

ובנוסף שתי שורות רישום:

```python
# brain/harvest/runner.py — type -> connector class
CONNECTORS = {"jira": JiraConnector, ..., "ado": AdoConnector}
# brain/canon/runner.py — type -> mapper
MAPPERS = {"jira": map_issues, ..., "ado": map_work_items}
```

שתי המפות ממופתחות ב-**type** ולא ב-**name**, כך ששני מופעי Jira (`jira-core`, `jira-eu`) הם שתי שורות ב-`sources.yaml` ואפס קוד.

---

## 2. הרשומה ב-`sources.yaml`

```yaml
sources:
  - name: ado                      # שם ייחודי; גם שם התיקייה ב-data/raw/<name>/
    type: ado                      # jira | confluence | git | ado | xray
    enabled: true                  # false = מחוץ ל-`--source all`, אבל עדיין אפשר `--source ado`
    base_url: https://dev.azure.com/acme/platform
    query: "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
    project_keys: [ADO]            # ← נכנס ל-allowlist של מפתחות ה-issue ב-canon
    auth_env: ADO_PAT              # שם משתנה סביבה. אף פעם לא הערך עצמו.
    auth_scheme: basic             # bearer | basic | url_token | none
    auth_user_env: ADO_USER        # ל-basic בלבד; ריק = צורת ה-PAT של ADO
    options:                       # כל מה שהוא כוונון פרוטוקול ולא זהות ארגונית
      api_version: '7.1'
      page_size: 200
```

**מה בדיוק עובר לקונפיג** (ולכן כבר אסור שיהיה קבוע בקוד):

- `base_url` — הכתובת של המערכת.
- `query` — ה-JQL / CQL / WIQL / חלון ה-commits. ל-git הפורמט הוא `<since>..<until>`, וסוף פתוח אומר "עד HEAD".
- `project_keys` — ה-allowlist. הרגקס שמזהה `ABC-123` לא יודע להבדיל בין `KAFKA-15123` ל-`UTF-8`; רק מפתח שמקור מוגדר כלשהו מחזיק יודע. ה-allowlist הוא **איחוד** של `project_keys` מכל המקורות ה-**enabled** ועוד הקידומות הסינתטיות.
- `document.title_pattern` — איך כותרת של דף הופכת ל-`Document.key`. בקפקא זה `KIP-(\d+)`; בארגון שקורא למסמכי העיצוב שלו `RFC-12` זו שורה אחת ב-YAML ולא שינוי קוד.
- `canon.issue_key_blacklist` — מפתחות שנראים אמיתיים ואינם. אצלנו `KAFKA-1`, ה-placeholder בתבנית ה-KIP.

**מה לא עובר לקונפיג:** סודות. אף פעם. `auth_env` הוא **שם של משתנה סביבה**, והרג'יסטרי דוחה ערך שלא נראה כמו שם משתנה — בדיוק כדי שטוקן לא ייכנס בטעות לקובץ שנכנס ל-git.

הקובץ נקרא ע"י `brain/common/yaml_mini.py` — תת-קבוצה של YAML (block mappings, block sequences, מחרוזות במרכאות, `[a, b]`). anchors, tags, block scalars ומסמכים מרובים **נדחים עם מספר שורה** במקום להיות מנוחשים.

---

## 3. הקונקטור

יורש מ-`BaseConnector` ומקבל את הרשומה מהרג'יסטרי. שלושה חוזים:

```python
class MyConnector(BaseConnector):
    name = "ado"            # ה-type; שם המופע נדרס מ-source.name ב-__init__
    page_stem = "workitems" # data/raw/<name>/workitems-0000.json

    def query_text(self, since) -> str: ...   # מה שנכתב ל-checkpoint, קריא לאדם
    def signature(self, since) -> str: ...    # hash של כל מה שמגדיר "אותה משיכה"
    def probe(self) -> ProbeResult: ...       # קריאה זולה אחת: נגיש? כמה?
    def fetch(self, since, checkpoint) -> Iterator[Page]: ...
```

- **`signature` הוא החוזה החשוב ביותר.** ה-checkpoint על הדיסק חתום בו. שינוי בו — גם כזה שמשמעותו זהה — פוסל 130MB של raw קיים ומושך אותו מחדש בשקט. `tests/test_registry.py` מקבע את שלוש החתימות של קפקא בדיוק בגלל זה.
- **`fetch` שומר checkpoint אחרי כל דף** (`checkpoint.advance(...)`), כדי שקריסה בדף 40 מתוך 56 תמשיך מ-40.
- **raw נשמר גולמי.** באג ב-mapper עולה הרצה חוזרת של `canon`, לא של `harvest`.
- **אף פעם לא לעשות glob לתיקיית raw.** רשימת הדפים היא `checkpoint.json` → `files`; ראו `brain/harvest/base.py`.

### auth

הקונקטור לא קורא משתני סביבה בעצמו:

```python
self.credentials = credentials_for(self.source)          # brain/harvest/auth.py
self.http = HttpFetcher(
    self.source.base_url,
    headers=self.credentials.headers(),                  # {} כשאין טוקן = אנונימי
    secrets=self.credentials.secrets,                    # מה שחייב להיות מוסתר בשגיאות
)
```

- `bearer` → `Authorization: Bearer <token>`
- `basic` → `Authorization: Basic base64(<user>:<token>)`; משתמש ריק = צורת ה-PAT של Azure DevOps
- `url_token` → הטוקן נכנס ל-URL של ה-clone (ל-git אין header), ו-`redact()` מוציא אותו מכל הודעת שגיאה
- משתנה **לא מוגדר** = אנונימי. משתנה **מוגדר וריק** = שגיאה, לא נסיגה שקטה: אנונימי מול Jira פרטי מוריד את תת-הקבוצה הציבורית ומדווח הצלחה.

---

## 4. ה-mapper

מקבל את הרשומות הגולמיות ומחזיר `Bundle`. שלושה כלים ב-`brain/canon/mappers/base.py`:

- `bundle.identity(source, key, display=..., email=...)` — רושם אדם ומחזיר את **מפתח המקור** (לא את ה-id). resolution יכתוב מחדש ids; רשומה שמצביעה על id טרום-resolution תירקב.
- `bundle.refs.collect(extract_refs(text), link_targets=[...])` — מפעיל את המנטיונים הדטרמיניסטיים, מסנן מול ה-allowlist, ומסמן ref שגם קישור פורמלי מצהיר עליו כ-`via="link"`.
- `FieldTracker` — סופר כל שדה גולמי שה-mapper **לא** קרא. זה מה שהופך את `unmapped_fields` בדוח למדוד ולא לזכור.

הכלל: אם לשדה מהמקור אין בית במודל הקנוני, הוא נרשם ב-`unmapped_fields` — **לא מרחיבים את המודל בלי brief**.

---

## 5. בדיקת ה-golden

```python
def test_golden_work_item_maps_to_the_expected_record():
    raw = load_fixture("ado", "raw_work_item.json")     # תשובה אמיתית, verbatim
    bundle = map_work_items([raw])
    assert bundle.workitems[0].model_dump(mode="json") == json.loads(
        (FIXTURES / "ado" / "expected_workitem.json").read_text()
    )
```

הזרימה: משיכה אמיתית אחת → שומרים דף גולמי ל-`tests/fixtures/<source>/` → מריצים את ה-mapper → **קוראים את הפלט בעיניים** → מקפיאים אותו. מכאן והלאה כל שינוי במיפוי הוא diff.

---

## 6. הרצה

```bash
uv run brain harvest --source ado     # רק המקור החדש; שאר data/raw/ לא נגעו בו
uv run brain canon                    # ממפה הכל; רשומות סינתטיות נשמרות
uv run brain load                     # MERGE בלבד; הרצה שנייה = 0 צמתים חדשים
```

`--source ado` עובד גם כשהמקור `enabled: false` — ככה בודקים קונקטור חדש פעם אחת לפני שהוא נכנס ל-`--source all`.

### מה הדוחות חייבים להראות

**`data/reports/harvest.json`**

| שדה | מה בודקים |
|---|---|
| `sources.<name>.records` / `pages` | כמה ירד בהרצה הזו. הרצה שנייה חייבת להיות 0. |
| `sources.<name>.checkpoint.done` | `true`. `false` = המשיכה נקטעה, יש להריץ שוב (תמשיך מאיפה שנעצרה). |
| `sources.<name>.errors` | כל retry וכל כישלון. `fatal: true` אחד = קוד יציאה 1. |
| `raw_layout.sources.<name>` | התיקייה הסמכותית, תיקיות ה-`since-*`, ומפתח ה-dedupe. |
| `sources.<name>.stats` | הסטטיסטיקה של המקור: כמה רשומות, כמה עם קישור פורמלי, כמה עם מפתח בטקסט. |

**`data/reports/canon.json`**

| שדה | מה בודקים |
|---|---|
| `counts.by_type` | חמשת הטיפוסים. `0` בטיפוס שציפיתם לו = ה-mapper לא רץ. |
| `refs.via_link` מול `refs.via_text` | כמה מהעקיבות פורמלית וכמה רק פרוזה. בקפקא: 865 מול 21,730. |
| `refs.top_removed` | מה שהרגקס חשב שהוא מפתח issue ונזרק. אם מפתח אמיתי שלכם שם — `project_keys` חסר אותו. |
| `unmapped_fields.<source>.dropped_with_data` | שדות שהמקור מילא וה-mapper זרק. הרשימה הזו היא התשובה הכנה ל"מה איבדנו". |
| `slices.<source>.duplicates` | חפיפה בין המשיכה המלאה למשיכות `--since`. |

**`data/reports/load.json`** — הרצה שנייה חייבת `nodes_created: 0, relationships_created: 0`. `dangling_refs` סופר הפניות למפתחות שמחוץ לפרוסה; זה מספר תקין, לא באג — אף שאילתת edge לא יוצרת צומת.

---

## 7. שלד: Azure DevOps

> ⚠️ **תבנית שלא נבדקה מול שרת חי.** כתובה מול התיעוד הציבורי של REST API 7.1. ה-`probe()` שלם; ה-mapper הוא stub מטופס למודל הקנוני. לפני שימוש: להריץ `probe()` פעם אחת מול הארגון שלכם, לשמור דף גולמי, ולכתוב את בדיקת ה-golden.

**מה שחשוב לדעת לפני שכותבים:** WIQL מחזיר **רק ids** (עד 20,000), ואת השדות מושכים בקריאה שנייה שמוגבלת ל-**200 ids** לקריאה. כלומר דף = 200 work items, וזה גם `options.page_size`. ה-PAT נשלח כ-HTTP basic עם **שם משתמש ריק**.

```python
"""brain/harvest/ado.py — Azure DevOps Boards (REST 7.1). UNTESTED TEMPLATE."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

from brain.harvest.auth import credentials_for
from brain.harvest.base import (
    BaseConnector, Checkpoint, HarvestError, HttpFetcher, Page, ProbeResult, signature_of,
)
from brain.harvest.registry import SourceConfig, get_registry

WIQL_PATH = "/_apis/wit/wiql"
ITEMS_PATH = "/_apis/wit/workitems"
ITERATIONS_PATH = "/_apis/work/teamsettings/iterations"
#: The batch endpoint refuses more than 200 ids. This is a server limit, not a choice.
MAX_IDS = 200
SINCE_FIELD = "System.ChangedDate"


def build_wiql(source: SourceConfig, since: date | None) -> str:
    """WIQL has no ORDER BY placement rule like JQL, but keep it last for readability."""
    query = source.query
    if since is not None:
        field = source.option("since_field", SINCE_FIELD)
        head, sep, order = query.partition(" ORDER BY ")
        head += f" AND [{field}] >= '{since.isoformat()}'"
        query = head + (sep + order if sep else "")
    return query


class AdoConnector(BaseConnector):
    name = "ado"
    page_stem = "workitems"

    def __init__(self, raw_dir: Path, *, source: SourceConfig | None = None,
                 http: HttpFetcher | None = None) -> None:
        super().__init__(raw_dir)
        self.source = source or get_registry().source(self.name)
        self.name = self.source.name
        self.api_version = str(self.source.option("api_version", "7.1"))
        self.page_size = min(self.source.int_option("page_size", MAX_IDS), MAX_IDS)
        self.credentials = credentials_for(self.source)  # basic, empty user + PAT
        self.http = http or HttpFetcher(
            self.source.base_url,
            headers=self.credentials.headers(),
            secrets=self.credentials.secrets,
        )

    def query_text(self, since: date | None) -> str:
        return build_wiql(self.source, since)

    def signature(self, since: date | None) -> str:
        return signature_of({
            "wiql": build_wiql(self.source, since),
            "expand": "all",
            "api_version": self.api_version,
            "page_size": self.page_size,
        })

    # -- probe: one WIQL call, ids only, nothing fetched -------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.post_json(   # NOTE: WIQL is POST — HttpFetcher needs it
                WIQL_PATH,
                params={"api-version": self.api_version, "$top": 1},
                json={"query": build_wiql(self.source, None)},
            )
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        ids = payload.get("workItems") or []
        return ProbeResult(ok=bool(ids), detail=f"{len(ids)} id(s) in the first page", total=None)

    # -- fetch: ids once, then fields in batches of 200 --------------------

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        if checkpoint.done:
            return
        ids = checkpoint.cursor.get("ids")
        if ids is None:
            payload = self.http.post_json(
                WIQL_PATH,
                params={"api-version": self.api_version},
                json={"query": build_wiql(self.source, since)},
            )
            ids = [int(w["id"]) for w in payload.get("workItems") or []]
        offset = int(checkpoint.cursor.get("offset", 0))
        index = checkpoint.pages

        while offset < len(ids):
            batch = ids[offset : offset + self.page_size]
            payload = self.http.get_json(
                ITEMS_PATH,
                {
                    "ids": ",".join(str(i) for i in batch),
                    "$expand": "all",           # fields + relations (links, parents)
                    "api-version": self.api_version,
                },
            )
            records = payload.get("value") or []
            path = self.write_page(since, index, payload)
            page = Page(index=index, records=records, path=path)
            offset += len(batch)
            checkpoint.advance(
                page=page, cursor={"ids": ids, "offset": offset}, total=len(ids)
            )
            yield page
            index += 1
        # Iterations are a second, tiny pull: they become Container(kind="sprint").
        yield from self._iterations(since, checkpoint, index)
        checkpoint.finish()

    def _iterations(self, since, checkpoint, index) -> Iterator[Page]:
        team = self.source.option("team")
        if not team:
            return
        payload = self.http.get_json(
            f"/{team}{ITERATIONS_PATH}", {"api-version": self.api_version}
        )
        records = payload.get("value") or []
        path = self.write_page(since, index, {"value": records, "kind": "iterations"})
        page = Page(index=index, records=records, path=path)
        checkpoint.advance(page=page, cursor=dict(checkpoint.cursor))
        yield page
```

```python
"""brain/canon/mappers/ado.py — Azure DevOps → the canonical five. UNTESTED TEMPLATE."""

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import extract_refs
from brain.canon.models import Container, Link, WorkItem

SOURCE = "ado"
MAPPED = frozenset({"System.Id", "System.WorkItemType", "System.Title", "System.State", ...})

#: `System.LinkTypes.Hierarchy-Reverse` is "my parent"; the graph's vocabulary is in
#: `brain/graph/mapping.py` and is closed — an unknown `rel` is counted, never invented.
REL_TO_LINK = {
    "System.LinkTypes.Hierarchy-Forward": ("parent", "in"),
    "System.LinkTypes.Hierarchy-Reverse": ("parent", "out"),
    "System.LinkTypes.Related": ("relates", "out"),
    "System.LinkTypes.Dependency-Forward": ("blocks", "out"),
    "System.LinkTypes.Dependency-Reverse": ("blocks", "in"),
    "Microsoft.VSTS.Common.TestedBy-Forward": ("tests", "in"),
}


def map_work_items(records) -> Bundle:
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    for record in records:
        if record.get("kind") == "iterations":       # the second pull
            _iteration(bundle, record)
            continue
        fields = record.get("fields") or {}
        tracker.observe(fields.items())
        key = f"ADO-{record['id']}"
        text = f"{fields.get('System.Title', '')}\n{fields.get('System.Description', '')}"
        bundle.workitems.append(
            WorkItem(
                id=f"{SOURCE}:{key}",
                key=key,
                source=SOURCE,
                type=str(fields.get("System.WorkItemType") or "Task"),
                title=str(fields.get("System.Title") or ""),
                description=str(fields.get("System.Description") or ""),
                status=str(fields.get("System.State") or ""),
                resolution=fields.get("System.Reason"),
                created=parse_dt(fields.get("System.CreatedDate")),
                updated=parse_dt(fields.get("System.ChangedDate")),
                # `uniqueName` is the stable identity; `displayName` is what a human sees.
                reporter=bundle.identity(
                    SOURCE,
                    (fields.get("System.CreatedBy") or {}).get("uniqueName"),
                    display=(fields.get("System.CreatedBy") or {}).get("displayName"),
                ),
                assignee=bundle.identity(
                    SOURCE,
                    (fields.get("System.AssignedTo") or {}).get("uniqueName"),
                    display=(fields.get("System.AssignedTo") or {}).get("displayName"),
                ),
                labels=[t.strip() for t in str(fields.get("System.Tags") or "").split(";") if t.strip()],
                links=_links(bundle, record.get("relations") or []),
                # AreaPath -> Container(kind="area"), IterationPath -> kind="sprint"
                components=[],
                refs=bundle.refs.collect(extract_refs(text)),
                raw_url=record.get("url"),
            )
        )
        bundle.container(SOURCE, "area", str(fields.get("System.AreaPath") or ""))
        bundle.container(SOURCE, "sprint", str(fields.get("System.IterationPath") or ""))
    bundle.stats = {"work_items": len(bundle.workitems), **tracker.report()}
    return bundle


def _links(bundle: Bundle, relations) -> list[Link]:
    out = []
    for relation in relations:
        mapped = REL_TO_LINK.get(str(relation.get("rel") or ""))
        if mapped is None:
            bundle.warn("unknown_ado_relation", rel=relation.get("rel"))
            continue
        kind, direction = mapped
        # `url` ends in `/workItems/42`; the key is the last segment.
        target = str(relation.get("url") or "").rsplit("/", 1)[-1]
        if target.isdigit():
            out.append(Link(type=kind, target=f"ADO-{target}", direction=direction))
    return out


def _iteration(bundle: Bundle, record) -> None:
    bundle.containers.setdefault(
        f"{SOURCE}:sprint:{record['name']}",
        Container(id=f"{SOURCE}:sprint:{record['name']}", source=SOURCE,
                  kind="sprint", name=str(record["name"]), parent=record.get("path")),
    )
```

**מלכודות שכבר ידועות מה-probe של 2026-09-03:** ארבעה ארגוני ADO ציבוריים החזירו 302 ל-Entra ו-`TF401232` על work items — כלומר **בלי PAT אין קריאה אנונימית**. זו הסיבה ש-ADO ירד מהתכנית ב-POC והוחלף בשכבה סינתטית.

---

## 8. שלד: Xray

> ⚠️ **תבנית שלא נבדקה מול שרת חי.** כתובה מול התיעוד הציבורי של Xray Cloud GraphQL v2.

**מה שחשוב לדעת:** ב-Xray Cloud ה-`Authorization` הוא JWT שמונפק מ-`POST /api/v2/authenticate` עם `client_id` + `client_secret`, והוא תקף שעה. אפשר להחזיק טוקן מוכן ב-`XRAY_TOKEN` (מה שהתבנית מניחה), או להנפיק אותו ב-`probe()` מ-`XRAY_CLIENT_ID`/`XRAY_CLIENT_SECRET`. `getTests` מוגבל ל-**100** תוצאות לעמוד.

```python
"""brain/harvest/xray.py — Xray Cloud GraphQL v2. UNTESTED TEMPLATE."""

AUTH_PATH = "/api/v2/authenticate"
GRAPHQL_PATH = "/api/v2/graphql"
PAGE = 100  # server maximum for getTests / getTestExecutions

TESTS_QUERY = """
query($jql: String!, $limit: Int!, $start: Int!) {
  getTests(jql: $jql, limit: $limit, start: $start) {
    total start limit
    results {
      issueId
      jira(fields: ["key", "summary", "description", "created", "updated", "reporter"])
      testType { name }
      steps { action data result }
    }
  }
}
"""

EXECUTIONS_QUERY = """
query($jql: String!, $limit: Int!, $start: Int!) {
  getTestExecutions(jql: $jql, limit: $limit, start: $start) {
    total start limit
    results {
      issueId
      jira(fields: ["key", "summary", "created", "updated"])
      testRuns(limit: 100) {
        results { status { name } test { issueId jira(fields: ["key"]) } defects }
      }
    }
  }
}
"""


class XrayConnector(BaseConnector):
    name = "xray"
    page_stem = "tests"

    def authenticate(self) -> str:
        """Mint a one-hour JWT from a client id/secret pair.

        Only needed when `$XRAY_TOKEN` does not already hold a valid token. The response
        body is the JWT as a *quoted JSON string*, which is why it is stripped.
        """
        client_id = os.environ.get("XRAY_CLIENT_ID")
        secret = os.environ.get("XRAY_CLIENT_SECRET")
        if not (client_id and secret):
            raise HarvestError(
                "Xray needs $XRAY_TOKEN, or $XRAY_CLIENT_ID + $XRAY_CLIENT_SECRET to mint one"
            )
        raw = self.http.post_text(
            AUTH_PATH, json={"client_id": client_id, "client_secret": secret}
        )
        return raw.strip().strip('"')

    def probe(self) -> ProbeResult:
        """One GraphQL call asking for `total` and zero results."""
        try:
            payload = self.http.post_json(
                GRAPHQL_PATH,
                json={
                    "query": "query($jql:String!){getTests(jql:$jql,limit:1){total}}",
                    "variables": {"jql": self.source.query},
                },
            )
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        if payload.get("errors"):
            # GraphQL answers 200 with an `errors` array. A probe that only checks the
            # status code reports a reachable server that returns nothing.
            return ProbeResult(ok=False, detail=str(payload["errors"])[:400])
        total = ((payload.get("data") or {}).get("getTests") or {}).get("total")
        return ProbeResult(ok=bool(total), detail=f"{total} tests match", total=total)
```

**המיפוי הקנוני** (זהה למה שהשכבה הסינתטית כבר מייצרת — ראו `brain/canon/synthetic_spec.md`):

| Xray | קנוני |
|---|---|
| `Test` | `WorkItem(type="Test", key="XT-<n>", source="xray")` |
| `TestPlan` / `TestSet` | `WorkItem(type="TestPlan"/"TestSet")` — **ולא** `Container`; ל-work item יש את הקישורים וההיררכיה |
| `TestExecution` | `WorkItem(type="TestExecution", key="XE-<n>")` |
| run (PASS/FAIL) | `Link(type="executes", target="XT-n")` על ה-execution + שורת `comments[]` `"XT-n: PASS\|FAIL (סיבה)"` |
| defect של FAIL | `Link(type="defect", target="<מפתח Jira אמיתי>")` |
| `steps[]` | לתוך `description` — צעדים ממוספרים; אין שדה קנוני לצעדים, ולכן זה נרשם ב-`unmapped_fields` |

---

## 9. `brain reset` — למחוק את דאטה הבדיקות

```bash
uv run brain reset --synthetic --yes   # רק השכבה הסינתטית; הקורפוס האמיתי נשאר טעון
uv run brain reset --graph --yes       # כל הצמתים; constraints ו-indexes נשארים
uv run brain reset --data --yes        # data/raw|canonical|batches|reports|eval
uv run brain reset --all --yes         # graph + data
```

- **בלי `--yes` שום דבר לא נמחק.** הפקודה מדפיסה את המניפסט של מה שהייתה מוחקת ויוצאת עם קוד 1, כדי שסקריפט לא יתבלבל בין סירוב לניקוי.
- **`--graph` לא מוחק את הסכמה.** constraints ו-indexes הם סכמה, לא דאטה: בלעדיהם ה-`brain load` הבא הוא label scan לכל `MERGE`, ובנייה מחדש שלהם היא החלק היחיד בטעינה שלא ניתן להריץ במקביל.
- **`--data` אף פעם לא נוגע ב-`data/fixtures/`.** זה הקורפוס המוקטן שעליו רצות הבדיקות ו-`make smoke`.
- **`--synthetic` הוא לא `WHERE n.synthetic = true` פשוט.** containers ממוזגים לפי `name`, ולכן `Component {name: "clients"}` הוא צומת אחד שגם Jira וגם שכבת ה-ADO טוענים לו, והכותב האחרון הוא שקבע את הדגל. הסריקה קוראת קודם את הקבצים הקנוניים כדי לדעת אילו מפתחות רשומה **אמיתית** עדיין מחזיקה, משאירה את הצמתים האלה ומאפסת להם את הדגל — בדיוק מה ש-`brain load` הבא היה כותב.
- אחרי `--all --yes`: `brain doctor` נשאר 7/7 (הוא בודק סביבה, לא תוכן), וכל ספירה בגרף היא 0. המניפסט נכתב ל-`data/reports/reset.json` — הקובץ היחיד שנשאר, והוא זה שמתעד שהניקוי קרה.

---

## 10. מ-POC לארגון אמיתי

**מה למחוק**

1. `uv run brain reset --all --yes` — הגרף ותיקיות הדאטה.
2. `sources.yaml`: להסיר את שלוש הרשומות של קפקא (`jira`, `confluence`, `git`) — או להשאיר אותן `enabled: false` כדוגמה חיה.
3. `synthetic:` ב-`sources.yaml`, `brain/synth/`, `brain/canon/synthetic_spec.md` — השכבה הסינתטית קיימת רק כדי לתת שכבות בדיקות ו-delivery שלדאטה הציבורי אין. בארגון אמיתי יש Xray ו-ADO אמיתיים, והיא מיותרת. אין צורך למחוק קוד: היא opt-in לגמרי (`tests/test_synthetic_optional.py` שומר על זה), ומספיק לא להריץ `brain synth`.
4. `data/fixtures/mini/` וה-golden ב-`tests/fixtures/` הם קפקא. הם ישארו נכונים כבדיקות רגרסיה של ה-mappers הקיימים; לכל mapper חדש צריך golden משלו.

**מה להגדיר**

| מה | איפה |
|---|---|
| כתובות, שאילתות, מפתחות פרויקט | `sources.yaml` |
| טוקנים | `.env` (או משתני סביבה של ה-CI). `.env` הוא gitignored. |
| תבנית מפתח של מסמכי עיצוב | `sources.yaml` → `sources[<wiki>].document.title_pattern` |
| מפתחות שנראים אמיתיים ואינם | `sources.yaml` → `canon.issue_key_blacklist` |
| Neo4j, Ollama, מודל ה-embedding | `.env` |
| קובץ רג'יסטרי אחר (למשל per-environment) | `SOURCES_FILE=/path/to/prod-sources.yaml` |

**מה לבדוק אחרי החיבור**

```bash
uv run brain doctor                    # 7/7
uv run brain harvest --source <name>   # פעם ראשונה
uv run brain harvest --source <name>   # פעם שנייה: 0 דפים, 0 רשומות
uv run brain canon && uv run brain load
uv run brain load                      # פעם שנייה: nodes_created 0
make check                             # כל הבדיקות offline
```

שתי ההרצות השניות הן הבדיקה האמיתית: `harvest` אידמפוטנטי דרך ה-checkpoint, `load` אידמפוטנטי דרך `MERGE`. אם אחת מהן לא מחזירה 0 — יש באג בזהות (signature, dedupe key, או מפתח MERGE), ולא בדאטה.
