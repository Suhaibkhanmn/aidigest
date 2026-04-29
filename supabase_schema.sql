create table if not exists telegram_subscribers (
  chat_id text primary key,
  active boolean not null default true,
  type text default '',
  username text default '',
  first_name text default '',
  last_name text default '',
  editions jsonb not null default '["first-light", "midday-note", "night-read"]'::jsonb,
  muted_topics jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists digests (
  name text primary key,
  mode text not null,
  date_slug text not null,
  brief_kind text not null,
  content text default '',
  telegram_content text default '',
  modified double precision not null,
  created_at timestamptz not null default now()
);

create table if not exists daily_index (
  id bigserial primary key,
  entry jsonb not null,
  recorded_at timestamptz not null default now()
);
