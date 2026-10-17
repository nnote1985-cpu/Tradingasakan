# Draft bot profiles

Settings → Bot profiles creates recipes for later implementation and research.
Nothing in this feature binds a recipe to a worker, starts a backtest, creates an
execution intent, or changes the YAML profiles in `configs/profiles`.

## Two ways to start

- **Preset:** snapshot a legacy profile, including every strategy, its timeframe,
  resolved parameters, pinned catalog versions and `average` or
  `main_with_filters` combination. Its decision clock remains daily. Entry and
  exit come from the combined signal. It is not silently converted into modular
  rules. Stop and risk fields are additional draft requirements, not a statement
  that the original preset used these settings.
- **Custom:** select one entry strategy and an optional trend filter, with an
  optional separate exit strategy. Entry uses increasing target weight, the
  filter allows a positive target weight, and exit uses decreasing target weight.
  The main timeframe is the entry clock. Each filter/exit may use its own closed
  bar timeframe. These are recipe definitions; no execution engine implements
  this composition yet.

Stops: entry-anchored ATR, highest-price-since-entry trailing ATR, or percentage
below entry. ATR uses the main timeframe. Risk / Trade is a percentage of bot
equity at risk at the stop, not order notional. Max Exposure is a percentage of
bot equity held in BTC. Values are percentages, not fractions (0.5 means 0.5%).
Form defaults are examples, not researched or approved trading settings.

## API and persistence

- `GET /api/v1/bot-profile-drafts`: allowlisted viewer or higher; latest revisions
  and a `can_edit` capability derived from server-side roles.
- `POST /api/v1/bot-profile-drafts`: operator or admin; validates the recipe and
  appends an immutable revision. `expected_revision` prevents lost updates (409).
  Reusing `request_id` with the same payload/author returns the original saved
  revision, including when a create response was lost. Different data with the
  same key conflicts.
- Status is always `draft`; `execution_supported` is always false. Unknown
  fields such as `bot_id`, `mode`, activation or trading credentials are refused.
- Stored in Supabase, table `trading.bot_profile_drafts` (migration
  `20261017085024_bot_profile_drafts.sql`): one append-only row per revision with
  time, author and content hash. RLS is on and only the API's service connection
  reads it; no worker or executor reads it. Moving the API to another host keeps
  the drafts.
- A save that loses the race to another editor gets 409. The form stays; the
  page shows the newer revision and lets the editor either save this form on top
  of it (as the next revision) or open the newer revision instead.
- Restarting the API or refreshing the browser preserves saved drafts. Unsaved
  editors survive app-tab navigation in the query cache, but are not saved on
  disk. Browser reload while the editor is mounted prompts about unsaved input.

Legacy YAML profile status is not proof that a running bot uses it. A future
binding/execution workflow needs a separate implementation and validation.
