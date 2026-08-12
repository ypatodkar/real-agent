# Agentic Cinema — official rules

Fetched from https://agentic-cinema.devpost.com/rules on 2026-08-12.
Summarised; the link is authoritative. **Re-read before submitting.**

---

## Hard constraints — these decide architecture

> **"Must use Google Cloud exclusively for AI/agent tools. No other AI models,
> agent frameworks, or AI APIs are permitted"** beyond Google Cloud and partner
> products.

**This bans LangGraph and LangChain outright** — they are agent frameworks. Not
a judgement call about libraries versus services; the rule names frameworks.
Google Cloud AI only: Gemini, Imagen, TTS, Vertex.

> **Grafana track: "Must actively use Grafana Cloud MCP server at runtime."**

Not a dashboard bolted on at the end. The running product has to call the
Grafana Cloud MCP server. This is the track's entry condition.

**Platform:** must be web, Android or iOS. A hosted, testable URL is required.

**Newly created during the contest period** (Jul 27 – Sep 7, 2026). No
modification of pre-existing work. Work done in August qualifies.

---

## Required submissions

| | |
|---|---|
| Hosted project URL | Judges must be able to test it |
| Text description | Features, technologies, data sources, findings |
| Public repo | GitHub/GitLab/Bitbucket, **with a licence file** |
| Demo video | YouTube or Vimeo, **≤ 3 minutes**, shows the thing working, English or subtitled |

No third-party advertising, logos or copyrighted material in the submission.

---

## Judging — four criteria, equally weighted

1. **Technological Implementation** — effective use of Google Cloud and partner services
2. **Design** — *"complete, coherent product experience versus proof-of-concept"*
3. **Potential Impact** — credible solution to a real problem for a real audience
4. **Quality of Idea** — creative, shows genuine understanding of the problem space

**Design is 25% of the grade.** A working pipeline that produces something
nobody would watch loses a quarter of the marks on its own.

---

## Dates

| | |
|---|---|
| Contest opens | Jul 27, 2026, 9:00 PT |
| **Deadline** | **Sep 7, 2026, 14:00 PT** |
| Judging | Sep 23 – Oct 7, 2026 |

---

## Prizes — per track

1st **$7,500** · 2nd **$4,500** · 3rd **$3,000**

Five tracks: IBM · **Grafana** · Parallel · ClickHouse · Replit.
Each has its own partner requirement; we are entering **Grafana**.

Teams up to 4 people.
