# Question templates for `question-forger`

Read this with `brain/eval/question_schema.json` open. The schema says what a question must
*look* like; this says what makes one worth measuring, and gives you a starting sentence for
every path shape the build can hand you.

Every batch input (`data/batches/questions/<shard>/NNN.in.json`) contains `paths[]`. Each
path carries the fields the templates below refer to:

| field | what it is |
|---|---|
| `path_id` | copy it into `source_path_id`, verbatim |
| `shape` | which table below applies |
| `type` | the question type — your question must have the same one |
| `anchors[]` | the keys your question **may** name. This is the subject |
| `answers[]` | the keys your question **must not** name. This is the answer |
| `nodes[]`, `edges[]` | the subgraph, with `role` telling you what each node is |
| `facts{}` | what the builder counted or computed (run statuses, dates, per-version totals) |
| `snippets[]` | raw chunk text, ≤400 chars, each with the `chunk_id` you cite |
| `truth[]` | for a synthetic path: the known-truth record, with the `truth:…` id to cite |
| `asks[]` | how many questions to write from this path, and in which language |
| `spare` | `true` means no question was asked for; use it only to replace an unusable path |

## The five rules the merge enforces

1. **Answer it from this path.** The gold answer must follow from the nodes, edges, facts,
   snippets and truth of the one path you name in `source_path_id`. Nothing else is checked
   against, so nothing else may be assumed.
2. **Never name an `answers[]` key in the question.** `Which tests cover KAFKA-16448?` is a
   question; `Did XT-10109 cover KAFKA-16448?` is the answer with a question mark. The same
   applies to any `gold_evidence` key that is not in `anchors[]`.
3. **Never restate the gold answer.** Eight consecutive shared words between the question
   and the gold answer is a rejection, in either language.
4. **`gold_evidence` is only what a right answer must rest on.** Node keys as the path
   spells them, `snippets[].chunk_id`, or a `truth:<section>:<index>` id. A related key that
   the answer does not need is noise: it lowers every strategy's precision and measures
   nothing.
5. **Identifiers stay untranslated.** `KAFKA-14649`, `KIP-848`, `XT-10007`, `clients`,
   `3.8.0`, a commit sha and an `Entity` id look identical in the Hebrew and English forms.
   That is what makes the cross-lingual comparison a comparison.

`difficulty`: 1 = one hop from the anchor, 2 = two hops or a join, 3 = three or more hops, an
aggregation, or a whole-corpus theme.

---

## traceability — "what is connected to what, and through what"

### shape `test_fix` · `Test-[:TESTS]->WorkItem<-[:RESOLVES]-Commit` (+ `HAS_RUN`)
Anchor: the work item. Answers: the tests, the commits, the executions.
`expected_strategy: s3` · difficulty 2.

| # | English | Hebrew |
|---|---|---|
| 1 | Which tests cover `{work_item}`, and what did their last recorded run say? | אילו טסטים מכסים את `{work_item}`, ומה אמרה ההרצה האחרונה שנרשמה להם? |
| 2 | What closed `{work_item}` — which commits, and when? | מה סגר את `{work_item}` — אילו קומיטים, ומתי? |
| 3 | `{work_item}` is marked `{status}`. What evidence in the corpus supports that — the fix or the test run? | `{work_item}` מסומן `{status}`. איזו ראיה בקורפוס תומכת בכך — התיקון או הרצת הטסט? |
| 4 | Is the coverage of `{work_item}` verified by a run, or only declared? | האם הכיסוי של `{work_item}` מאומת בהרצה, או רק מוצהר? |

`gold_evidence`: the test keys, the commit shas, and the work item key.

### shape `story_kip` · `WorkItem-[:REFERENCES]->Document`
Anchor: the KIP. Answers: the work items.
`expected_strategy: s3` · difficulty 1–2.

| # | English | Hebrew |
|---|---|---|
| 1 | Which work items deliver `{document}`? | אילו פריטי עבודה מממשים את `{document}`? |
| 2 | Who is tracking `{document}` outside Jira — is there an ADO item for it? | מי עוקב אחרי `{document}` מחוץ ל-Jira — האם יש פריט ADO עבורו? |
| 3 | How much of `{document}` is still open work, and where? | כמה מהעבודה על `{document}` עדיין פתוחה, ואיפה? |

`gold_evidence`: the document key and the work item keys.

### shape `renamed_test` · truth: the same thing under two names
Anchor: the test key and the phrase the test uses. Answer: the Jira key and its phrase.
`expected_strategy: s1` · difficulty 2 · `gold_source: truth`.

| # | English | Hebrew |
|---|---|---|
| 1 | Test `{test}` exercises "{test_phrase}". Which Kafka issue is that, and what does the issue call it? | הטסט `{test}` בודק את "{test_phrase}". על איזה issue של Kafka מדובר, ואיך ה-issue קורא לזה? |
| 2 | "{test_phrase}" appears in the test suite. Is the same behaviour tracked under a different name in the issue tracker? | "{test_phrase}" מופיע בסוויטת הטסטים. האם אותה התנהגות נרשמת תחת שם אחר במעקב ה-issues? |
| 3 | If I search the issues for "{test_phrase}" I find nothing. What is it called there? | אם אחפש ב-issues את "{test_phrase}" לא אמצא כלום. איך זה נקרא שם? |

`gold_evidence`: the `truth:renames:<i>` id, plus the test key.

---

## impact — "what would a change touch"

### shape `blast_radius` · `Component<-[:IN_COMPONENT]-WorkItem(open)` (+ `TESTS`, `REFERENCES`)
Anchor: the component. Answers: the open items, their tests, the documents they reference.
`expected_strategy: s2` · difficulty 2–3.

| # | English | Hebrew |
|---|---|---|
| 1 | If we change `{component}`, which open work items, tests and KIPs are affected? | אם נשנה את `{component}` — אילו פריטי עבודה פתוחים, טסטים ומסמכי KIP מושפעים? |
| 2 | How much open work is there in `{component}` right now, and how much of it is covered by tests? | כמה עבודה פתוחה יש ב-`{component}` כרגע, וכמה ממנה מכוסה בטסטים? |
| 3 | What is the riskiest open item in `{component}`, and what does it depend on? | מהו הפריט הפתוח המסוכן ביותר ב-`{component}`, ובמה הוא תלוי? |
| 4 | Which open work in `{component}` has design documentation behind it and which has none? | לאיזו עבודה פתוחה ב-`{component}` יש תיעוד תכן מאחוריה ולאיזו אין? |

`gold_evidence`: the component name and the work item keys (plus test/document keys the
answer actually needs).

### shape `text_only_link` · truth: a link the prose states and no field does
Anchor: the mirror item. Answer: the linked item.
`expected_strategy: s1` · difficulty 2 · `gold_source: truth`.

| # | English | Hebrew |
|---|---|---|
| 1 | Which Kafka issue does `{mirror_item}` actually track? | על איזה issue של Kafka `{mirror_item}` באמת עוקב? |
| 2 | If `{mirror_item}` slips, what else slips with it? | אם `{mirror_item}` יידחה, מה עוד נדחה איתו? |
| 3 | `{mirror_item}` has no link field pointing anywhere. Does its text name something it depends on? | ל-`{mirror_item}` אין שדה קישור שמצביע לשומקום. האם הטקסט שלו מזכיר משהו שהוא תלוי בו? |

`gold_evidence`: the `truth:text_only_links:<i>` id and the mirror item key.

### shape `duplicate_test` · truth: two keys, one check
Anchor: the first test. Answer: its duplicate.
`expected_strategy: s1` · difficulty 2 · `gold_source: truth`.

| # | English | Hebrew |
|---|---|---|
| 1 | If the behaviour `{test}` checks changes, how many tests have to be updated? | אם ההתנהגות ש-`{test}` בודק משתנה, כמה טסטים צריך לעדכן? |
| 2 | Does the suite check the same thing as `{test}` more than once? | האם הסוויטה בודקת את אותו דבר כמו `{test}` יותר מפעם אחת? |

`gold_evidence`: the `truth:duplicate_tests:<i>` id and the anchor test key.

---

## rationale — "why was it decided this way"

### shape `decision_rejects` · `Document-[:DECIDES]->Entity` and `Document-[:REJECTS]->Entity`
Anchor: the KIP. Answers: the decision entities and the rejected alternatives.
`expected_strategy: s3` · difficulty 2.

| # | English | Hebrew |
|---|---|---|
| 1 | Why was the design in `{document}` chosen? | למה נבחר התכן ב-`{document}`? |
| 2 | What alternatives did `{document}` reject, and on what grounds? | אילו חלופות `{document}` דחה, ומאיזה נימוק? |
| 3 | What did `{document}` decide about `{topic}`, and what did it turn down to get there? | מה `{document}` החליט לגבי `{topic}`, וממה ויתר בדרך? |
| 4 | Was the alternative `{document}` rejected turned down for correctness or for cost? | האם החלופה ש-`{document}` דחה נדחתה מטעמי נכונות או מטעמי עלות? |

`gold_evidence`: the document key, the `Entity` ids, and the `chunk_id` the reason is
quotable from.

### shape `motivation` · `Document-[:DECIDES]->Entity-[:MOTIVATED_BY]->Entity(Problem)`
Anchor: the KIP. Answer: the problem.
`expected_strategy: s3` · difficulty 2–3.

| # | English | Hebrew |
|---|---|---|
| 1 | What problem motivated `{document}`? | איזו בעיה הניעה את `{document}`? |
| 2 | What was broken before `{document}`, in the authors' own words? | מה היה שבור לפני `{document}`, במילים של הכותבים עצמם? |
| 3 | `{document}` makes several decisions. Do they all answer the same problem? | `{document}` מקבל כמה החלטות. האם כולן עונות לאותה בעיה? |

`gold_evidence`: the document key, the problem `Entity` ids, and a `chunk_id`.

---

## global — "what is this corpus about"

### shape `community_theme` · `Community(title, summary)<-[:IN_COMMUNITY]-member`
Anchor: nothing by key — ask about the **theme in words**. Answers: the member keys.
`expected_strategy: s5` · difficulty 3.

| # | English | Hebrew |
|---|---|---|
| 1 | What are the main themes in the work around {theme in words}? | מהם הנושאים המרכזיים בעבודה סביב {הנושא במילים}? |
| 2 | Across the whole corpus, what is the recurring problem behind {theme in words}? | לרוחב כל הקורפוס, מהי הבעיה החוזרת מאחורי {הנושא במילים}? |
| 3 | If I had to brief a new engineer on {theme in words}, what are the three things to know? | אם הייתי צריך לתדרך מהנדס חדש על {הנושא במילים}, מהם שלושת הדברים שחשוב לדעת? |
| 4 | Which KIPs and issues make up the body of work on {theme in words}? | אילו מסמכי KIP ו-issues מרכיבים את גוף העבודה על {הנושא במילים}? |

A global question is the one shape where the anchor is a *description*, not a key: paraphrase
the community's `title` in your own words rather than quoting it, and never name the
`L<level>-<id>`. `gold_evidence`: the community id plus the member keys the answer names.

---

## temporal — "what was true when"

### shape `ownership_timeline` · `WorkItem-[:ASSIGNED_TO {valid_from,valid_to}]->Person` + `StatusChange`
Anchor: the work item (and a date from `facts`). Answers: the people and the status changes.
`expected_strategy: s6` · difficulty 2.

| # | English | Hebrew |
|---|---|---|
| 1 | Who was assigned to `{work_item}` over time, and in what order? | מי היה משויך ל-`{work_item}` לאורך זמן, ובאיזה סדר? |
| 2 | What was the status of `{work_item}` on {date}? | מה היה הסטטוס של `{work_item}` בתאריך {date}? |
| 3 | How long did `{work_item}` sit in its longest state, and who held it then? | כמה זמן `{work_item}` שהה במצב הארוך ביותר שלו, ומי החזיק בו אז? |
| 4 | Did `{work_item}` change hands before or after it first moved out of Open? | האם `{work_item}` החליף ידיים לפני או אחרי שיצא לראשונה מ-Open? |

`gold_evidence`: the work item key, the `Person` ids, the `StatusChange` ids.

### shape `release_window` · `Component<-[:IN_COMPONENT]-WorkItem-[:FIX_VERSION]->Version`
Anchors: the component and the two version names. Answers: the released work items.
`expected_strategy: s6` · difficulty 2–3.

| # | English | Hebrew |
|---|---|---|
| 1 | What changed in `{component}` between {version_low} and {version_high}? | מה השתנה ב-`{component}` בין {version_low} ל-{version_high}? |
| 2 | How much work shipped in `{component}` in {version_high} compared with {version_low}? | כמה עבודה נשלחה ב-`{component}` בגרסה {version_high} לעומת {version_low}? |
| 3 | Is the work in `{component}` for {version_high} finished, or still partly open? | האם העבודה ב-`{component}` לגרסה {version_high} הושלמה, או שחלקה עדיין פתוח? |

`gold_evidence`: the component name, the version names, the work item keys.

### shape `stale_state` · truth: which of two records of the same work is current
Anchor: the mirror item. Answer: the Jira item and its real status.
`expected_strategy: s6` · difficulty 2 · `gold_source: truth`.

| # | English | Hebrew |
|---|---|---|
| 1 | `{mirror_item}` still reads `{ado_status}`. Is that the real state of the work? | `{mirror_item}` עדיין מציג `{ado_status}`. האם זה המצב האמיתי של העבודה? |
| 2 | Which record of the work behind `{mirror_item}` is up to date, and which is behind? | איזו רשומה של העבודה שמאחורי `{mirror_item}` מעודכנת, ואיזו מפגרת? |
| 3 | Can I close `{mirror_item}`, or is there work left on it? | אפשר לסגור את `{mirror_item}`, או שנשארה בו עבודה? |

`gold_evidence`: the `truth:stale_states:<i>` id and the mirror item key.

---

## A worked example

Path (abridged) from a `test_fix` batch:

```json
{
  "path_id": "test_fix:KAFKA-16448",
  "type": "traceability",
  "anchors": ["KAFKA-16448"],
  "answers": ["XT-10109", "8d11d957…"],
  "facts": {"tests": 1, "commits": 4, "run_statuses": ["PASS"]},
  "snippets": [{"chunk_id": "4f0a74a9…", "parent_key": "KAFKA-16448", "text": "…"}]
}
```

A good question:

```json
{
  "id": "q004",
  "type": "traceability",
  "lang": "en",
  "question": "Which tests cover KAFKA-16448, and did their last recorded run pass?",
  "gold_answer": "One Xray test, XT-10109, covers it; its most recent execution passed. Four commits resolved the issue, the last of them 8d11d957.",
  "gold_evidence": ["KAFKA-16448", "XT-10109", "8d11d957…"],
  "difficulty": 2,
  "expected_strategy": "s3",
  "source_path_id": "test_fix:KAFKA-16448",
  "gold_source": "graph"
}
```

Why it passes: it names only the anchor, its gold rests on keys the path offered, and the
answer is a fact the sources state — so the vector baseline that never sees the graph has a
fair chance at it, which is the entire point of measuring the graph against it.

The same question in Hebrew keeps every identifier:

> אילו טסטים מכסים את KAFKA-16448, והאם ההרצה האחרונה שנרשמה להם עברה?
