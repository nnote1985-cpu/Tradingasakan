-- Draft bot recipes (docs/bot-profile-drafts.md): versioned, never read by a worker or the executor.
-- Each save appends a revision; (profile_id, revision) is the lost-update guard and request_id makes a
-- retried save return the revision it already wrote. Private like the rest of `trading`: RLS on,
-- service_role only.
create table trading.bot_profile_drafts (
  profile_id uuid not null,
  revision   integer not null check (revision >= 1),
  body       jsonb not null check (body ->> 'status' = 'draft'),
  digest     text not null check (digest ~ '^[0-9a-f]{64}$'),
  saved_by   uuid not null,
  saved_at   timestamptz not null default now(),
  request_id uuid not null unique,
  primary key (profile_id, revision)
);

create trigger bot_profile_drafts_append_only before update or delete on trading.bot_profile_drafts
  for each row execute function trading.forbid_change();

grant select, insert on trading.bot_profile_drafts to service_role;
alter table trading.bot_profile_drafts enable row level security;
