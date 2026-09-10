# Second Unit — Screenplay Foundation

## 1. Current flow

1. Screenplay stays locked until the Outline is approved.
2. The filmmaker chooses **Mostly visual**, **Narration-led**, **Character-led**, or **Hybrid**.
3. Gemini converts every accepted beat into one or more proposed scenes.
4. Every scene separates scene heading, visible action, narration, and dialogue.
5. The filmmaker can edit generated scenes; edits are versioned in SQLite.
6. The filmmaker explicitly approves the Screenplay before Breakdown unlocks.

## 2. Authority rules

- The approved Outline is the source of truth.
- Gemini may author screenplay language, but cannot silently replace the premise,
  characters, ending, or accepted beat order.
- Generated scenes remain proposals.
- Every accepted Outline beat must be represented before a draft can be saved.
- Invalid model output saves no partial screenplay.

## 3. Handoff

Approval locks every active scene and makes it the source for Production Breakdown.
Once a Breakdown exists, the source Screenplay cannot be reopened silently.

## 4. Later improvements

Scene reordering, scene alternatives, and Fountain/PDF export are not built yet.
