# שיעור 10 — מודולריות: `sources.yaml`, טוקן שלא נשאר בשום קובץ, ו-`brain reset`

**תאריך:** 2026-09-07 · **תכנית:** Plan 1 (שלב 11 + סבב התיקונים 11b) · **מודול בקורס:** 10 ("פרודקשן: עלויות, אבטחה, פרטיות וניטור")

## מה עשינו

שלב 11 הוא ההבטחה של ADR-0005: *"בסוף המשתמש רוצה למחוק את דאטה הבדיקות ולחבר מערכות אמיתיות (Jira/ADO/Xray/Confluence של הארגון) בקלות."* בפועל זה שלושה דברים נפרדים שנבנו יחד — **רג'יסטרי מונחה-קונפיג** (`sources.yaml` + `brain/harvest/registry.py`), **auth שמגיע רק ממשתני סביבה** (`brain/harvest/auth.py`), ו-**`brain reset`** עם ארבעה scopes (`brain/reset.py`) — ועוד מדריך עברי, `docs/guides/adding-a-connector.md`, עם שני שלדי קונקטור (Azure DevOps ו-Xray) שכתובים מול התיעוד הציבורי ו**מעולם לא רצו**.
כל URL, JQL/CQL, `project_keys`, תבנית כותרת (`KIP-(\d+)`) ו-blacklist של מפתחות (`KAFKA-1`) יצאו מ-Python לקובץ אחד בשורש. ההוכחה שזה לא שינה כלום היא `data/reports/modularity.json`: **חמישה sha1 של הקבצים הקנוניים מול ה-digests שנרשמו לפני הרפקטור — `matches_baseline: true`, `drift: {}`.**
הסקירה החזירה **fix-required** עם שלושה blockers, וסבב 11b (`0f23d83…f34198f`) סגר את שלושתם: (1) **`synthetic` לא הגיע לגרף החי** — 13,846 chunks נשאו `null` ו-`brain reset --synthetic` נפל לנפילה-אחורה של "האב נעלם", מה שהשאיר **1,882 orphans** בסקירה; נכתבה פקודת backfill אידמפוטנטית, `brain chunk --stamp-synthetic`, ו-`Chunk.synthetic` ירד ל-**0 nulls / 1,939 true**. (2) **דליפת token דרך `TimeoutExpired`** — `subprocess` מדפיס את ה-argv בחריגות שלו, וה-argv של `git clone` מכיל את הטוקן; ה-`except` ב-`brain/harvest/git.py::_run` הפך ל**רחב בכוונה** (`except Exception … from None`), והקונקטור מריץ `git remote set-url origin <public>` מיד אחרי ה-clone כדי שהטוקן לא יישאר ב-`.git/config`. (3) **שני מקורות מאותו type התנגשו** — `id` ייחודי הפך לחובה, `data/raw/<id>/`, ו-`source_id` על הרשומה הקנונית.
`--synthetic` מסיר בדיוק **2,442 רשומות** (1,849 workitems, 420 persons, 173 containers, 0 documents, 0 changes) ועוד **321 שורות** ב-`resolution_ledger.json` שמצביעות על זהות סינתטית. **מפתחות קנוניים נשארו בלי prefix** — `KAFKA-1` משני מופעי Jira הוא צומת אחד — וזה מתועד כמגבלה ידועה (§5 במדריך), לא כבאג פתוח.

## למה ככה (הקישור לקורס)

**המודול מציג את הקונפיג כברירת מחדל, וזה בדיוק מה שהעברנו.** ה-`settings.yaml` של MS GraphRAG בקורס נראה כך: `api_key: ${GRAPHRAG_API_KEY}` — כלומר גם שם, **הקונפיג מחזיק את השם של משתנה הסביבה ולא את הערך**. `sources.yaml` שלנו עושה את אותה הפרדה ואוכף אותה: `auth_env` נבדק מול `re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*")`, ומחרוזת שלא נראית כמו שם משתנה נדחית עם ההודעה *"Secrets never live in sources.yaml"* — כי הקובץ הזה נכנס ל-git.

**"מודולרי" כאן הוא ארבעה פריטים, ולא יותר.** ADR-0005 §1 מגדיר: *"גבול מודול = המודל הקנוני. קונקטור חדש = `Connector` (probe/fetch/checkpoint) + mapper ל-5 הטיפוסים + בדיקת golden. שום קוד אחרי `canon` לא יודע מאיפה הדאטה."* המיפוי לקוד:

| מה כותבים | איפה |
|---|---|
| רשומה בקונפיג | `sources.yaml` — `id`, `type`, `base_url`, `query`, `project_keys`, `auth_env`, `auth_scheme`, `options` |
| קונקטור | `brain/harvest/<source>.py` — `query_text()`, `signature()`, `probe()`, `fetch()`; כותב raw גולמי ל-`data/raw/<id>/` |
| mapper | `brain/canon/mappers/<source>.py` — raw → `WorkItem`/`Document`/`Person`/`Change`/`Container` |
| בדיקת golden | `tests/test_canon_<source>.py` — תשובה גולמית אמיתית אחת מול רשומה קנונית קפואה |
| שתי שורות רישום | `CONNECTORS` ב-`brain/harvest/runner.py`, `MAPPERS` ב-`brain/canon/runner.py` — שתיהן ממופתחות ב-**type** |

והמפתח ב-**type** הוא מה שהופך "שני מופעי Jira" ל**שתי שורות YAML ואפס קוד**. מה ששולם עבור זה הוא `SOURCE_TYPES` — קבוצה סגורה של חמישה types ב-`registry.py`, ו-`Source` שהוא `Literal` סגור ב-`brain/canon/models.py`: type חדש לגמרי כן דורש שתי שורות קוד, וזו הכרעה מודעת (מפתח לא מוכר נדחה במקום להיווצר בשקט).

**"מחיקה = גזירה מחדש" — הקורס אומר את זה על GDPR, ואצלנו זה `--synthetic`.** המודול כותב: *"כשהעובדות ממסמך שנמחק כבר מוזגו לתיאורי ישויות ולסיכומי קהילות, מחיקת המסמך לא מספיקה"*, והצ'ק-ליסט דורש *"פרובננס מלא (ישות→צ'אנק→מסמך) + מסלול מחיקת GDPR בדוק"*. `brain reset --synthetic` הוא בדיוק המסלול הזה על שכבה אחת: הוא לא יכול להיות `WHERE n.synthetic = true` פשוט, כי הדגל חייב לנסוע לאורך אותה שרשרת פרובננס — רשומה קנונית → chunk → entity — ובכל חוליה הוא **נגזר** ולא מועתק:

* `Chunk.synthetic` = יש הורים **וכולם** סינתטיים (`_DERIVE_SYNTHETIC` ב-`brain/chunk/graph.py`);
* `Entity.synthetic` = יש chunks ראייה שורדים **וכולם** סינתטיים (`_DERIVE_ENTITY_SYNTHETIC` ב-`brain/extract/graph.py`);
* חוליה שלישית שהקורס לא מזכיר ואנחנו נתקלנו בה: **צומת משותף הוא לא סינתטי.** `Component {name: "clients"}` הוא צומת אחד ש-Jira ושכבת ה-ADO הסינתטית שניהם טוענים לו, והכותב האחרון קבע את הדגל. הסריקה קוראת קודם את `containers.jsonl` כדי לדעת מה רשומה **אמיתית** עדיין מחזיקה, משאירה את הצמתים האלה ו**מאפסת להם את הדגל** — בדיוק מה ש-`brain load` הבא היה כותב.

**"מנטרים את האינדוקס, לא רק את השאילתות" — ולכן הראיה היא דוח, לא הרצה ירוקה.** ה-docstring של `brain/modularity.py` אומר למה: *"the claim that has to survive review is not 'the tests pass', it is **'the canonical corpus is byte-for-byte what it was'** — a registry that builds an equivalent-but-different query produces a corpus that is *almost* the same, and almost is invisible until a retrieval answer cites a key that moved."* לכן הדוח סופר שלושה דברים: sha1/sha256 של חמשת הקבצים מול baseline, כמה `--synthetic` היה מוחק, ו**מה הקונפיג באמת נפתר אליו** — כולל `query_signatures` (`jira: 5da028abe2be`, `confluence: 162b225214ef`, `git: c7899d52babc`), שהן ה-hash שה-checkpoint על הדיסק חתום בו: שינוי בהן פוסל את ה-raw הקיים ומושך 130MB מחדש בשקט. הן מקובעות פעמיים — ב-`tests/test_registry.py` וב-`tests/test_modularity.py`.

**איפה הקורס לא נותן כיסוי: redaction של credentials.** מודול 10 מדבר על אבטחה (`Text2Cypher` כמשטח תקיפה, משתמש DB לקריאה בלבד, הרעלת קורפוס) ועל טוקנים רק כשורה בצ'ק-ליסט. הוא לא מדבר על מה שקורה כשהטוקן שלך נכנס ל-`data/reports/harvest.json` — קובץ שמדביקים ל-issue. זה הפער שסבב 11b סגר, וההסבר בקוד: `git` מקבל את הטוקן ב-argv, וספריית התקן מדפיסה את ה-argv בחריגות שלה, אז *"the point is not to name the exceptions that quote `argv` (`TimeoutExpired` does, `FileNotFoundError` does not), it is that **nothing** leaves here unredacted."*

## מספרים

| מדד | ערך |
|---|---|
| commits בשלב | `48cf7f8` (registry+auth), `d8ce2c0` (reset), `c265047` (מדריך) · תיקוני 11b: `0f23d83…f34198f` (`0f23d83` backfill, `eb35f55` redaction, `5f18084` id ייחודי, `355134c` space לפי המקור, `e2666c1`, `f5f9e79`, `5d0451b`, `f34198f`) |
| **byte-identity (`matches_baseline`)** | **`true`** · `drift: {}` · הדוח נוצר `2026-09-07T15:06:28+00:00` |
| חמשת ה-sha1 | `workitems.jsonl` **`3472864688c1f933bea89786489ea02ccac04100`** (19,209,121B / 3,265 רשומות) · `documents.jsonl` **`4e74e1a0df814bfd7487580cba7f278c10d5276d`** (16,037,914B / 1,391) · `persons.jsonl` **`73ae016f6c5f7695c8051bb71fdd0d4ac1430bae`** (421,579B / 2,187) · `changes.jsonl` **`c4870ae497cc240196997a8e5d29c52eeef0fad1`** (11,000,717B / 12,133) · `containers.jsonl` **`4bb4dc6bd9f6b779f2ecc909ae8faa1e8974101e`** (41,700B / 290) |
| הקפאה שנייה, ניידת | `MINI_SHA1` ב-`tests/test_modularity.py` — 5 sha1 של `data/fixtures/mini` (למשל `workitems.jsonl` `418c8cf10ecb8ece933a87f70a0b0dfa9501e195`); רצה בכל clone, בניגוד ל-`BASELINE_SHA1` שנבדק רק כש-`data/canonical` קיים |
| `sources.yaml` | **5 מקורות** — 3 enabled (`jira`, `confluence`, `git`) + 2 templates disabled (`ado`, `xray`) · `same_type_ids: {}` |
| allowlist שנגזר | **6 מפתחות**: `ADO, KAFKA, XE, XP, XS, XT` · blacklist: `KAFKA-1` · `synthetic_key_prefixes`: `ADO→ado`, `XT/XE/XP/XS→xray` |
| `allowlist_warnings` | **2** — `ado` ו-`xray` כבויים, אבל 5 מפתחות הפרויקט שלהם עדיין הופכים התאמות טקסט ל-issue refs (`enabled` שולט על משיכה, לא על זיהוי) |
| `query_signatures` | `jira` **`5da028abe2be`** · `confluence` **`162b225214ef`** · `git` **`c7899d52babc`** |
| **`--synthetic` מוחק** | **2,442 רשומות**: workitems **1,849** · persons **420** · containers **173** · documents **0** · changes **0** · ועוד **321** שורות ledger |
| **backfill — chunks** | `Chunk.synthetic`: **null 13,846 → 0**, מהם **1,939 true** · אימות אחרי: `null 0`, `wrong 0` |
| **backfill — entities** | `Entity.synthetic`: **9,038 ישויות, null 0, wrong 0, `synthetic_after` 0** — אף ישות אינה סינתטית (Phase A של extract לא חילץ מ-chunks סינתטיים) |
| ריצת האימות (`data/reports/chunk.json → synthetic_stamp`) | `2026-09-07T14:21:01+00:00` · `applied: false` · `duration_s` **0.19** · `stamped: {chunks: 0, entities: 0}` — אידמפוטנטיות מוכחת: ריצה שנייה מטביעה 0 |
| orphans אחרי `--synthetic --dry-run` | **0** (בסקירה: **1,882**) |
| labels ש-`--graph` מוחק | **16** — 12 `PRIMARY_LABELS` + 4 `EXTRA_LABELS` (`Chunk`, `IndexMeta`, `Entity`, `Community`) · `DETACH DELETE` בפרוסות של **1,000** שורות · constraints ו-indexes **נשארים** |
| תיקיות ש-`--data` מרוקן | **5** — `raw`, `canonical`, `batches`, `reports`, `eval` · `PROTECTED_DIRS = {fixtures}` |
| `--all` | `--graph` + `--data` בלבד — **לא** כולל `--synthetic` |
| נעילה | `data/reset.lock` דרך `os.open(..., O_CREAT\|O_EXCL\|O_WRONLY)` · נלקחת **רק** כשיש `--yes`; dry-run לא נועל · עוצרת reset שני בלבד — load/chunk/extract לא לוקחים אותה |
| בלי `--yes` | מניפסט מודפס, אפס מחיקות, **קוד יציאה 1** (אותה מוסכמה ב-`--stamp-synthetic --dry-run`) |
| סכמות auth | **4** — `bearer`, `basic`, `url_token`, `none` · לא מוגדר = אנונימי · **מוגדר וריק = `AuthError`** |
| בדיקות redaction | **19** ב-`tests/test_harvest_auth.py`, כולל `test_a_clone_timeout_does_not_print_the_token`, `test_no_exception_from_subprocess_leaves_git_unredacted`, `test_the_clone_does_not_leave_the_token_in_git_config`, `test_a_failing_stats_pass_cannot_leak_either`, ו-end-to-end `test_a_leaking_connector_still_writes_a_clean_harvest_report` |
| בדיקות בשלב | **93 unit** — `test_reset.py` 29 · `test_registry.py` 28 · `test_harvest_auth.py` 19 · `test_yaml_mini.py` 11 · `test_modularity.py` 6 — ו-**7 live** ב-`tests/live/test_reset_live.py`, כולן במרחב התוויות `_ResetTest` |
| תלות שלא הוספה | `yaml_mini.py` במקום PyYAML — תת-קבוצת YAML; anchors, tags, block scalars ומסמכים מרובים **נדחים עם מספר שורה** |

## מה הפתיע

- **הדגל היה בקוד, לא בגרף.** `Chunk.synthetic` ו-`Entity.synthetic` נכתבים ע"י ה-chunker וע"י `extract merge` **בזמן הכתיבה** — והקורפוס עבר chunking וחילוץ לפני שהמאפיין היה קיים. התוצאה: 13,846 chunks עם `null`, כל הישויות עם `false`, ו-`brain reset --synthetic` שאין לו על מה להתאים ולכן נופל אחורה ל"האב נעלם" — נפילה שמוצאת את ה-chunks ומשאירה את הישויות מאחור, **1,882 orphans**. הלקח לא היה "לתקן את הכותב" אלא **לכתוב פקודה שגוזרת מחדש מהגרף**: `brain chunk --stamp-synthetic` שואל את הגרף את אותה שאלה שהכותב שואל את הקלט שלו, לא צריך embedder, לא מוחק כלום, וריצה שנייה מטביעה 0. **feature flag שנוסף אחרי שהדאטה נכתב הוא לא feature flag — הוא backfill שעוד לא נכתב.**
- **הדגל של הישות חייב לגזור את ה-chunks, לא לקרוא אותם** (`f5f9e79`). הניסוח המתבקש — "ישות סינתטית אם כל `Chunk.synthetic` שלה true" — היה מדווח, מול גרף שה-chunks בו עדיין `null`, "אין שינויים בישויות". זה בדיוק ה-all-clear השקרי שהתיקון נועד לסגור. לכן `_CHUNK_IS_SYNTHETIC` נגזר מההורים **בתוך** שאילתת הישויות, ושתי ההטבעות מסודרות chunks-ואז-entities דווקא כדי שהשנייה לא תכתוב `false` על הכול שוב — *"which is precisely how the graph got into this state."*
- **הישויות התבררו כלא-סינתטיות בכלל — 0 מתוך 9,038.** ה-blocker דרש להטביע `Entity.synthetic`, וכשהטבענו התשובה הייתה אפס: Phase A של extract רץ על 1,713 סקשנים של KIP ו-1,030 תיאורים, ולא נגע ב-chunks של השכבה הסינתטית. אפס הוא תשובה נכונה ולא "לא נבדק" — אבל רק **אחרי** שהגזירה נכתבה, כי לפניה כל 9,038 הישויות נשאו `false` שנכתב ע"י merge שרץ מול chunks ריקים. `false` מדוד ו-`false` שנכתב בחוסר ידיעה נראים זהה בגרף.
- **`git clone` משאיר את הטוקן בדיסק גם אחרי שהתהליך מת.** ה-redaction בשלוש שכבות (`HttpFetcher._safe`, `git.py::_run`, `BaseConnector.collected_errors`) מטפל במחרוזות שיוצאות מהתהליך — ולא בכך ש-`git` שומר את ה-URL עם ה-credential ב-`.git/config`. הפתרון עולה שורה אחת (`git remote set-url origin <public>` מיד אחרי ה-clone, ואחרי זה הקונקטור לא מושך שוב אף פעם — `git log` מקומי בלבד), אבל הוא לא היה קיים עד שמישהו כתב בדיוק את הבדיקה `test_the_clone_does_not_leave_the_token_in_git_config`.
- **`except Exception` רחב הוא כאן ההחלטה הנכונה, ובדרך כלל הוא לא.** הבחירה מנומקת בקוד: `TimeoutExpired.__str__` מדפיסה את כל הפקודה, `FileNotFoundError` לא, ו*"a new failure mode in a future Python must not be a new leak"*. גם `from None` הוא חלק מההגנה — הוא זורק את החריגה המקורית מה-traceback מאותה סיבה. שלוש השכבות **כולן נדרשות**: `stats()` שנכשל וחריגה שרירותית מספרייה שלישית עוברות דרך גבול הדוח, לא דרך ה-HTTP.
- **משתנה סביבה מוגדר-וריק הוא שגיאה, לא אנונימיות.** נסיגה שקטה לאנונימי מול Jira פרטי מורידה את תת-הקבוצה הציבורית ו**מדווחת הצלחה** — `export JIRA_TOKEN=` שגוי הופך לקורפוס חלקי שאף דוח לא מסמן. `credentials_for()` זורק `AuthError` עם ההוראה המדויקת ("unset it to harvest anonymously").
- **`--all` הוא `--graph` + `--data`, ולא כולל `--synthetic`.** קריאה סבירה של הדגל אומרת הפוך. בפועל זה עקבי — `--all` מוחק את הכול, כך שלמחוק "רק את הסינתטי" בתוכו הוא חסר משמעות — אבל `if all_: graph = data = True` ב-`brain/cli.py` הוא שורה שכדאי לראות לפני שמריצים.
- **ה-dry-run של ה-orphans שואל שאילתה אחרת מהריצה האמיתית, ובכוונה.** בהרצה בפועל ההורים כבר נמחקו, ולכן "אין `HAS_CHUNK` נכנס" היא התשובה. ב-dry-run כולם עוד שם ואותה שאילתה הייתה מחזירה 0 — *"a manifest promising to delete nothing and then deleting thousands"*. לכן החיזוי סופר את ה-chunks שההורה שלהם **עומד** להימחק, פלוס אלה שכבר יתומים, **פחות** אלה שסריקת התווית כבר לקחה — והחיסור הזה הוא מה שמונע ספירה כפולה של 1,939 ה-chunks הסינתטיים אחרי ה-backfill. זה גם הדבר היחיד בשלב שנדרש commit דוקומנטציה נפרד (`f34198f`) כדי לומר את הסיבה נכון.
- **הדגל של הקונטיינר המשותף מתאפס, ולא רק "לא נמחק".** `_wipe_synthetic_graph` מריץ את ה-`SET n.synthetic = false` על הצמתים המוגנים **ללא תנאי**, ולא בתוך הענף שמוחק — כי יש מקרה שבו לתווית אין מה למחוק **דווקא בגלל** שכל הצמתים הסינתטיים בה משותפים, וזה בדיוק המקרה שהדגל שלו עוד חייב להתאפס.
- **`splitlines()` חותך רשומות אמיתיות בקורפוס הזה.** `synthetic_counts` מנמק למה הוא מאייטר על ה-handle: הקבצים נכתבים ב-`ensure_ascii=False`, ותיאור שמכיל `U+2028` או `U+0085` מגיע לקובץ גולמי — `str.splitlines()` שובר על שניהם וחותך רשומת JSON באמצע. *"Real records in this corpus do exactly that."*
- **`source_id` נשמט מה-JSON כשהוא שווה ל-`type`, וזה מה שהציל את ה-byte-identity.** אילו השדה היה מסריאליזציה תמיד, כל שורה בכל קובץ קנוני הייתה משתנה — וה-`matches_baseline` שהשלב הזה קיים כדי להוכיח היה נפסל **לתמיד**. `owner_of` ב-`brain/canon/runner.py` הוא הקורא היחיד שצריך לפתור את שני המקרים; ל-`Change`, שאין לו בכלל שדה `source`, יש fallback דטרמיניסטי (`change_owner`) במקום שגיאה.
- **השדה נקרא `name` עד 11b, והשינוי קיבל הודעת שגיאה משלו.** רשומה ישנה לא נדחית עם "unknown key `name`" (שנקרא כמו typo) אלא עם *"the field is now `id` (unique per source, and the directory under data/raw/). Rename it."*

## מה היינו משנים

- **`synthetic` היה צריך להיות בסכמה של `Chunk` בשלב 06, לא backfill בשלב 11.** המחיר בפועל היה נמוך (0.19 שניות לסריקה, בוליאני אחד לצומת), אבל הפקודה `brain chunk --stamp-synthetic` תישאר בקוד לנצח בשביל אירוע חד-פעמי, וכל מי שיקרא אותה בעוד שנה ישאל למה היא קיימת. **כל מאפיין שמישהו יסתמך עליו כדי למחוק חייב להיוולד עם הצומת** — ADR-0005 §5 אמר "`synthetic=true` על כל רשומה" ב-2026-09-03, שלושה שלבים לפני ש-`Chunk` בכלל קיים, ואף אחד לא תרגם את זה לצומת החדש.
- **הנעילה מגינה על החצי הלא-נכון.** `reset_lock` עוצרת שני resets — התרחיש שמפיק את המצב הגרוע ביותר — אבל **אף שלב אחר לא לוקח אותה**, ולכן `brain load` או `brain chunk` שרץ במקביל יכתוב לתוך מצב חצי-מחוק. הקוד כן כן ישר לגבי זה (ה-docstring וה-מניפסט שניהם אומרים "the pipeline must be IDLE"), אבל שורה בטקסט היא לא נעילה. נעילה משותפת ב-`GraphContext` שכל פקודה כותבת לוקחת — או לפחות בדיקת קיום שמסרבת להתחיל — הייתה עולה מעט והופכת את האזהרה לאכיפה.
- **`BASELINE_SHA1` הוא קבוע בקוד שמתאר קורפוס במכונה אחת.** `data/` לא מקומם ב-git, ולכן חמשת ה-sha1 של הקורפוס האמיתי חיים כ-dict ב-`brain/modularity.py`, וה-live test שבודק אותם רץ רק אם `data/canonical` קיים. זה מתועד וכנה — אבל המשמעות היא שבמכונה אחרת החלק שבודק 45MB פשוט מדלג, ומה שבאמת רץ הוא ה-mini fixture (20 שורות). היינו מעדיפים שה-baseline יהיה **קובץ דוח מגובה-git** (`data/reports/` שנשמר, או `docs/report/`) ושהבדיקה תהיה diff בין שני דוחות — אותה חוזק הוכחה, בלי ערך שהופך את הקוד לתלוי-מכונה.
- **מפתחות קנוניים בלי namespace — הכרעה נכונה שנשארת חוב.** ההכרעה מנומקת: namespace על `WorkItem.key` משנה כל מפתח בכל דוח, בכל ref ובכל ציטוט עתידי של MCP, ולכן דורש brief משלו. אבל התוצאה היא שהתמיכה ב"שני Jira" **חלקית**: `id` ייחודי, `data/raw/<id>/` ו-`source_id` פתרו את הבעלות; `KAFKA-1` משני מופעים עדיין קורס לצומת אחד, והרשומה האחרונה שנכתבת מנצחת. ההמלצה במדריך ("מרחבי מפתחות זרים") היא מדיניות תפעולית שאף בדיקה לא אוכפת — `Registry.same_type_ids()` **מדווח** ולא **מסרב**. סף שמפיל את הריצה כששני מקורות enabled מאותו type חולקים `project_keys` היה הופך את המגבלה מ"כתובה במדריך" ל"בלתי אפשרית בטעות".
- **שני השלדים לא רצו אף פעם, וזה כתוב בהם — אבל אין להם probe אמיתי.** ADO ו-Xray מסומנים ⚠️ "לא יתקמפל כמו שהוא", וחסר להם `HttpFetcher.post_json` (WIQL ו-GraphQL שניהם POST). ה-probe של 2026-09-03 כבר גילה ש-ADO ציבורי מחזיר 302 ל-Entra ו-`TF401232`, כלומר בלי PAT אין קריאה בכלל. מה שהיינו משנים: **להוסיף `post_json` בפועל** — צעד קטן, עם pacing/backoff/`_safe` שכבר קיימים — כדי ששלד יהיה "חסר credentials" ולא "חסר קוד".
- **`entities_without_evidence` מזהיר ולא מטפל.** ההכרעה לא למחוק נכונה (resolution אולי מיזג לתוך הצומת זהויות אמיתיות, ו-`MERGE` לא יודע לפרק את זה), והמניפסט אפילו ממליץ על התיקון (`brain extract merge` חוזר). אבל אין פקודה שסוגרת את הלולאה, ואין קריטריון שאומר כמה זה יותר מדי. אחרי ה-backfill המספר הוא 0, ולכן זה לא כאב — **המספר הבא שיהיה שונה מאפס יגלה שאין מי שיטפל בו.**

## מה זה מלמד (המתכנן)

**1. "מודולרי" זה לא ארכיטקטורה, זה ארבעה קבצים ובדיקה.** שורה ב-`sources.yaml`, connector, mapper, golden test. המבחן: Jira שני נכנס בלי שינוי קוד (0 שורות), ADO נכנס עם שלושה קבצים. כל מה שמעבר לזה — registry, auth, reset — קיים כדי שהארבעה יספיקו. הקורס קורא לזה "מודל קנוני כמפתח לכל מערכת": ה-mapper מתרגם *אל* המודל, ושום דבר במורד הזרם לא יודע מאיפה הרשומה הגיעה.

**2. byte-identity הוא מבחן הרפקטור היחיד שאי אפשר לרמות.** חמישה sha1 של הקנוני לפני ואחרי — אותם בתים. לכן `source_id` מושמט כשהוא שווה ל-type: שדה חדש שמופיע בכל שורה היה מבטל את המבחן לתמיד. הסוכן בחר לשמור על המבחן במחיר סריאליזציה פחות "נקייה". זו ההכרעה הנכונה: מבחן שאפשר להריץ שווה יותר משדה עקבי.

**3. token דולף מהמקום שלא חשבת עליו.** לא מהלוג — מ-`TimeoutExpired.__repr__`, מגוף 4xx, מ-`.git/config` אחרי clone. 19 בדיקות, והמכריעה היא "קונקטור עוין שזורק חריגה עם ה-token שלו בתוכה" → ה-token לא בדוח, לא ב-stdout, לא ב-exit. redaction בגבול הדוח, לא בכל קריאה: כי הגבול הוא מקום אחד, והקריאות הן מאה.

**4. דגל `synthetic` צריך להיות *derived*, לא *copied*.** 13,846 chunks נולדו לפני שהשדה היה קיים. אם ה-backfill היה קורא את הדגל מהצומת, ריצה על גרף ישן הייתה מייצרת "0 סינתטיים" ושותקת. גזירה מהאב (HAS_CHUNK) בכל ריצה = dry-run מדויק גם על גרף לא-מסומן, ו-idempotent בהגדרה. אותו עיקרון כמו `member_hash` בקהילות: האמת נגזרת מהמבנה, לא נשמרת לצידו.

**5. גבול מתועד עדיף על פתרון חצי.** שני Jira עם `KAFKA-1` באותו מפתח = צומת אחד. אפשר היה להוסיף prefix לכל מפתח — ולשבור כל regex, כל ציטוט וכל בדיקה. ההכרעה: id ייחודי למקור, `source_id` על הרשומה, ומגבלה במדריך עם המלצה (project_keys זרים). POC שיודע מה הוא לא פותר עדיף על POC שפותר הכל בערך.

**6. reset הוא חלק מהמוצר.** `--synthetic` מוריד בדיוק 2,442 רשומות + 1,939 chunks + ledger, ומשאיר Container משותף. בלי זה, כל ניסוי בהערכה (Plan 3) היה מזהם את הגרף לצמיתות. היכולת למחוק בביטחון היא מה שמאפשר להוסיף בביטחון.

**מה זה קובע ל-Plan 2/3:** האחזור מסמן `props.synthetic` על כל פריט, ו-`include_synthetic=False` מוריד את השכבה — כך אפשר למדוד את אותה שאלה עם ובלי Xray/ADO; ADO אמיתי נכנס אחרי ה-POC עם ה-skeleton מהמדריך ו-PAT דרך env.
