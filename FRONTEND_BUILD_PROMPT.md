# Compliance Memory System — Frontend Build Prompt

This is a build specification for a React frontend against the existing FastAPI backend
(`api.py` + `set1_graph_builder/`, `set2_law_monitor/`, `set3_gap_analysis/`, `set4_remediation/`).
Follow it as a step-by-step build plan — complete and verify each step before starting the next.

## What this app is

An internal compliance review tool. A human reviews AI-drafted changes to a company's policy
document (proposed because of gaps found against applicable law), accepts/rejects/edits them
inline, reviews the consolidated final draft, and commits it — which is version-controlled like
a GitHub PR. The tone should read as **professional, trustworthy, and quiet** — this is enterprise
compliance software, not a consumer app. Nothing flashy; clarity and confidence over decoration.

## Core principle: boring code, no over-engineering

- Functional components + hooks only. No class components.
- No state management library beyond what's listed below (no Redux, no Zustand) — TanStack Query
  handles server state; local UI state is `useState`/`useReducer` where actually needed.
- No premature abstraction. Three similar components is fine; don't build a generic factory for it.
- Plain JavaScript (JSX), not TypeScript, to match the backend's own "keep it simple" convention —
  use PropTypes for lightweight type-safety on component props instead.
- Every component's styling lives in its own colocated CSS Module file. No inline `style={}` props
  except for truly dynamic, computed-at-runtime values (e.g. a progress bar width).
- No CSS-in-JS libraries (styled-components, emotion). CSS Modules only.

## Tech stack

- **Vite + React 18** (`npm create vite@latest -- --template react`) — fast, standard, minimal config.
- **React Router v6** for page navigation.
- **TanStack Query (React Query) v5** for all server data fetching/caching/mutations.
- **CSS Modules** for component styles, plus one global stylesheet for resets/tokens.
- **PropTypes** for prop validation.
- No UI component library (MUI, Chakra, etc.) — build the small set of primitives this app
  actually needs (Button, Card, Badge, Spinner, TextArea) so styling stays fully controlled and
  the dependency footprint stays small.

## Design system

Create `src/styles/tokens.css` defining CSS custom properties on `:root`. This is the single
source of truth for the palette — every component references these variables, never a hardcoded
hex value.

**Palette** — a deep indigo/slate primary (trust, professionalism — standard in fintech/legal/
compliance tooling) with a subtle violet-to-blue gradient reserved for primary CTAs and header
accents only, not overused:

```css
:root {
  /* Primary */
  --color-primary-50:  #eef2ff;
  --color-primary-100: #e0e7ff;
  --color-primary-500: #6366f1;
  --color-primary-600: #4f46e5;
  --color-primary-700: #4338ca;
  --color-primary-gradient: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);

  /* Neutrals (slate) */
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-border: #e2e8f0;
  --color-text-primary: #0f172a;
  --color-text-secondary: #475569;
  --color-text-muted: #94a3b8;

  /* Semantic - severity */
  --color-critical: #dc2626;
  --color-high: #ea580c;
  --color-medium: #d97706;
  --color-low: #2563eb;

  /* Semantic - status */
  --color-success: #16a34a;
  --color-success-bg: #f0fdf4;
  --color-danger: #dc2626;
  --color-danger-bg: #fef2f2;
  --color-pending: #d97706;
  --color-pending-bg: #fffbeb;

  /* Diff view */
  --color-diff-add-bg: #dcfce7;
  --color-diff-add-text: #166534;
  --color-diff-remove-bg: #fee2e2;
  --color-diff-remove-text: #991b1b;

  /* Spacing scale (4px base) */
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-6: 24px; --space-8: 32px; --space-12: 48px;

  /* Typography */
  --font-sans: -apple-system, "Segoe UI", Roboto, Inter, sans-serif;
  --font-mono: "SF Mono", "Fira Code", monospace; /* for policy/diff text */
  --font-size-sm: 0.875rem; --font-size-base: 1rem; --font-size-lg: 1.125rem;
  --font-size-xl: 1.5rem; --font-size-2xl: 2rem;

  /* Radii/shadows */
  --radius-md: 8px; --radius-lg: 12px;
  --shadow-sm: 0 1px 2px rgba(15,23,42,0.06);
  --shadow-md: 0 4px 12px rgba(15,23,42,0.08);
}
```

**Responsive**: mobile-first. Breakpoints as CSS custom media via plain `@media (min-width: 640px)`
(sm), `768px` (md), `1024px` (lg), `1280px` (xl) — standard Tailwind-equivalent breakpoints, used
directly in each component's CSS Module, no framework needed.

## Folder structure

```
frontend/
  src/
    api/
      client.js              # thin fetch wrapper: base URL, JSON parsing, error normalization
      graphBuilder.js         # POST /graph-builder/run
      lawIngest.js             # POST /law-ingest/run
      documents.js              # POST /documents/ingest (multipart)
      gapAnalysis.js             # POST /gap-analysis/run
      remediation.js              # all /remediation/* endpoints
    components/
      Button/Button.jsx + Button.module.css
      Card/Card.jsx + Card.module.css
      Badge/Badge.jsx + Badge.module.css          # severity/status pills
      Spinner/Spinner.jsx + Spinner.module.css
      TextArea/TextArea.jsx + TextArea.module.css
      DiffLine/DiffLine.jsx + DiffLine.module.css   # inline add/remove rendering
      SuggestionCard/SuggestionCard.jsx + .module.css
      Layout/Layout.jsx + Layout.module.css          # nav + page shell
    hooks/
      useGraphBuilder.js
      useLawIngest.js
      useDocumentUpload.js
      useGapAnalysis.js
      useSuggestions.js
      useFinalDraft.js
      useRemediationHistory.js
    pages/
      DashboardPage/DashboardPage.jsx + .module.css
      DocumentsPage/DocumentsPage.jsx + .module.css
      GapAnalysisPage/GapAnalysisPage.jsx + .module.css
      SuggestionsPage/SuggestionsPage.jsx + .module.css
      FinalDraftPage/FinalDraftPage.jsx + .module.css
      HistoryPage/HistoryPage.jsx + .module.css
    styles/
      tokens.css
      global.css                # reset + base element styles, imports tokens.css
    App.jsx                       # router setup
    main.jsx
  .env.example                     # VITE_API_BASE_URL=http://localhost:8000
  package.json
```

## Backend API reference (integrate exactly these — nothing invented)

Base URL from `import.meta.env.VITE_API_BASE_URL`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/health` | - | `{"status": "ok"}` |
| POST | `/graph-builder/run` | - | Long-running (LLM calls, can take minutes). Returns `{status, entity_count, relationship_count, ...}` |
| POST | `/law-ingest/run` | - | Returns `{status, chunk_count, collection}` |
| POST | `/documents/ingest` | multipart `file` | Returns `{status, chunk_count, collection, document}` |
| POST | `/gap-analysis/run` | - | Long-running. Returns `{status, gap_count, final_gaps: [...], ...}` |
| POST | `/remediation/draft` | - | Long-running (re-runs gap analysis + drafts). Returns `{gap_count, suggestion_count, suggestions: [...]}` |
| GET | `/remediation/suggestions` | - | Returns array of suggestion objects (see shape below) |
| PATCH | `/remediation/suggestions/{id}` | `{status, final_text?}` | `status` is `"accepted"` \| `"rejected"` \| `"edited"` |
| GET | `/remediation/final-draft` | - | Returns `{text}` |
| PATCH | `/remediation/final-draft` | `{text}` | Human override of the whole draft |
| POST | `/remediation/finalize` | - | Commits + re-triggers Set 1. Returns `{status, commit, message, graph_rebuild}` |
| GET | `/remediation/history?limit=20` | - | Returns array of `{commit, date, message}` |
| GET | `/remediation/diff?from_rev=X&to_rev=HEAD` | - | Returns `{diff}` (raw git diff text) |

**Suggestion object shape**:
```json
{
  "id": "suggestion-gap-data-breach-notification",
  "gap_id": "gap-data-breach-notification",
  "gap_title": "Data Breach Notification Gap",
  "severity": "high",
  "operation": "replace",
  "anchor_excerpt": "verbatim original text",
  "suggested_text": "AI's proposed replacement",
  "final_text": "same as suggested_text until human edits it",
  "rationale": "why this addresses the gap",
  "verifier_confidence": 0.85,
  "status": "pending",
  "created_at": "ISO timestamp"
}
```

**Long-running endpoints** (`graph-builder/run`, `gap-analysis/run`, `remediation/draft`) can take
1-5+ minutes (multiple LLM calls). Every mutation hook wrapping these must show a persistent
loading state (not just a button spinner) and must NOT time out client-side prematurely — set
fetch/query timeouts generously (e.g. 10 minutes) or omit them.

## Pages, in build order

Build and manually verify each page against the real running backend before moving to the next.

### Step 1 — Project scaffold
`npm create vite@latest frontend -- --template react`, install `react-router-dom` and
`@tanstack/react-query`, set up `src/styles/tokens.css` + `global.css`, `src/api/client.js`
(base fetch wrapper with JSON handling + error normalization into `{message, status}`), and
`App.jsx` with React Router routes for all 6 pages (empty placeholders initially) inside a
shared `Layout` component (top nav: Dashboard / Documents / Gaps / Suggestions / Final Draft /
History). Verify: app runs, all routes navigate, nav highlights the active page.

### Step 2 — Shared components
Build `Button`, `Card`, `Badge` (severity + status variants, colored per the tokens above),
`Spinner`, `TextArea`. Each with its own `.module.css`. No page logic yet — verify each in
isolation (temporarily render a few variants on the Dashboard page to eyeball them).

### Step 3 — Dashboard page
Cards for each pipeline stage (Graph Builder, Law Ingest, Gap Analysis, Draft Remediation), each
with a "Run" button wired to its mutation hook, a status area (idle/running/success/error), and
a result summary once complete. This is the operational control panel.

### Step 4 — Documents page
Drag-and-drop (or plain file input, keep it simple) upload for PDF/DOCX/TXT/MD, wired to
`POST /documents/ingest`, shows ingestion result (chunk count).

### Step 5 — Gap Analysis page
Displays results of the last gap analysis run (list of gaps: title, description, severity badge,
cited law sections, related entities). Read-only page, no actions - Set 4 is where action happens.

### Step 6 — Suggestions page (the core inline-review UI)
For each pending suggestion: a `SuggestionCard` showing the gap title/severity, the anchor
excerpt with a strikethrough/red diff treatment (`operation: "replace"`) or a plain green
insertion (`operation: "insert_after"`), the AI's `suggested_text` in green, and three actions:
Accept, Reject, Edit (Edit reveals a `TextArea` pre-filled with `suggested_text`, submitting
calls PATCH with `status: "edited"` and the new `final_text`). Already-resolved suggestions
(accepted/rejected) show a muted/collapsed state, not hidden entirely - the human should be able
to see and change their mind.

### Step 7 — Final Draft page
Full document text (monospace, `--font-mono`) in a large editable `TextArea`, pre-filled from
`GET /remediation/final-draft`. A "Save Edits" button (PATCH), and a prominent "Finalize" button
that requires a confirmation step (this is a real, consequential action - commits to git and
rebuilds the graph) before calling `POST /remediation/finalize`. Show the returned commit hash
and graph-rebuild summary on success.

### Step 8 — History page
List of commits from `GET /remediation/history` (hash short-form, date, message). Selecting two
commits (or one + "current") fetches and renders `GET /remediation/diff` with the same
add/remove diff styling as the Suggestions page, for visual consistency.

### Step 9 — Responsive + polish pass
Verify every page at mobile (375px), tablet (768px), and desktop (1280px) widths - nav should
collapse to a hamburger or bottom bar below `md`. Add empty states (e.g. "No suggestions yet -
run Draft Remediation from the Dashboard") and error states (backend unreachable, request
failed) to every page that fetches data. Confirm loading states are visible and non-janky for
the long-running pipeline endpoints specifically.

## Explicit non-goals for this build

- No authentication/login - out of scope per the backend's own CLAUDE.md.
- No offline support, no service worker.
- No animation library - CSS transitions only, and sparingly (this is compliance software, not
  a marketing site).
- No dark mode unless requested separately - ship the light palette above well, don't split
  effort building two.
