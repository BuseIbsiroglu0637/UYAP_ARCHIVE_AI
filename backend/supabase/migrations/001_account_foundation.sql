-- UYAP Archive AI - M1 Account Foundation
-- Apply to a dedicated Supabase project.
create extension if not exists pgcrypto;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  status text not null default 'active' check (status in ('active','suspended','deleted')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.plans (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  active boolean not null default true,
  monthly_price numeric(12,2),
  yearly_price numeric(12,2),
  currency text not null default 'TRY',
  device_limit integer not null default 1,
  created_at timestamptz not null default now()
);

create table if not exists public.plan_entitlements (
  plan_id uuid not null references public.plans(id) on delete cascade,
  entitlement_key text not null,
  enabled boolean not null default false,
  limit_value bigint,
  primary key(plan_id, entitlement_key)
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan_id uuid not null references public.plans(id),
  status text not null default 'trialing' check (status in ('trialing','active','past_due','canceled','expired')),
  started_at timestamptz not null default now(),
  current_period_end timestamptz,
  trial_ends_at timestamptz,
  provider text,
  provider_customer_id text,
  provider_subscription_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists subscriptions_user_status_idx on public.subscriptions(user_id,status);

create table if not exists public.devices (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null,
  device_name text,
  platform text,
  extension_version text,
  agent_version text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique(user_id, device_id)
);

create table if not exists public.usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null,
  quantity numeric not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists usage_events_user_created_idx on public.usage_events(user_id,created_at desc);

alter table public.profiles enable row level security;
alter table public.subscriptions enable row level security;
alter table public.devices enable row level security;
alter table public.usage_events enable row level security;
alter table public.plans enable row level security;
alter table public.plan_entitlements enable row level security;

create policy "profiles_select_own" on public.profiles for select to authenticated using (auth.uid()=user_id);
create policy "profiles_update_own" on public.profiles for update to authenticated using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "subscriptions_select_own" on public.subscriptions for select to authenticated using (auth.uid()=user_id);
create policy "devices_select_own" on public.devices for select to authenticated using (auth.uid()=user_id);
create policy "usage_select_own" on public.usage_events for select to authenticated using (auth.uid()=user_id);
create policy "plans_read" on public.plans for select to authenticated using (active=true);
create policy "plan_entitlements_read" on public.plan_entitlements for select to authenticated using (true);

create or replace function public.handle_new_user_profile() returns trigger
language plpgsql security definer set search_path=public as $$
begin
  insert into public.profiles(user_id,full_name)
  values(new.id, coalesce(new.raw_user_meta_data->>'full_name',''))
  on conflict (user_id) do nothing;
  return new;
end; $$;

drop trigger if exists on_auth_user_created_profile on auth.users;
create trigger on_auth_user_created_profile after insert on auth.users
for each row execute function public.handle_new_user_profile();

insert into public.plans(code,name,active,device_limit)
values ('starter','Başlangıç',true,1),('professional','Profesyonel',true,2),('office','Ofis',true,10)
on conflict(code) do update set name=excluded.name, device_limit=excluded.device_limit;

with e(code,key,enabled,lim) as (
 values
 ('starter','archive',true,null),('starter','archive_local',true,null),('starter','archive_final_package',true,null),('starter','ai',false,null),('starter','ai_summary',true,10),('starter','ai_timeline',false,null),('starter','ai_search',false,null),
 ('professional','archive',true,null),('professional','archive_local',true,null),('professional','archive_final_package',true,null),('professional','ai',true,null),('professional','ai_summary',true,null),('professional','ai_timeline',true,null),('professional','ai_search',true,null),('professional','multi_device',true,2),
 ('office','archive',true,null),('office','archive_local',true,null),('office','archive_final_package',true,null),('office','ai',true,null),('office','ai_summary',true,null),('office','ai_timeline',true,null),('office','ai_search',true,null),('office','multi_device',true,10),('office','office_users',true,null)
)
insert into public.plan_entitlements(plan_id,entitlement_key,enabled,limit_value)
select p.id,e.key,e.enabled,e.lim from e join public.plans p on p.code=e.code
on conflict(plan_id,entitlement_key) do update set enabled=excluded.enabled,limit_value=excluded.limit_value;

create or replace function public.get_account_snapshot() returns jsonb
language sql stable security definer set search_path=public as $$
with current_sub as (
  select s.*,p.code plan_code,p.name plan_name,p.device_limit
  from public.subscriptions s join public.plans p on p.id=s.plan_id
  where s.user_id=auth.uid() and s.status in ('trialing','active','past_due')
  order by case s.status when 'active' then 1 when 'trialing' then 2 else 3 end, s.created_at desc limit 1
), fallback_plan as (
  select id,code,name,device_limit from public.plans where code='starter' limit 1
), chosen as (
  select cs.plan_id id,cs.plan_code code,cs.plan_name name,cs.device_limit,cs.status subscription_status,cs.current_period_end from current_sub cs
  union all
  select fp.id,fp.code,fp.name,fp.device_limit,'none'::text,null::timestamptz from fallback_plan fp where not exists(select 1 from current_sub)
), ents as (
  select jsonb_object_agg(pe.entitlement_key, case when pe.limit_value is null then to_jsonb(pe.enabled) else jsonb_build_object('enabled',pe.enabled,'limit',pe.limit_value) end) data
  from public.plan_entitlements pe join chosen c on c.id=pe.plan_id
)
select jsonb_build_object(
 'user', jsonb_build_object('id',u.id,'email',u.email,'full_name',pr.full_name),
 'plan', jsonb_build_object('code',c.code,'name',c.name,'device_limit',c.device_limit),
 'subscription', jsonb_build_object('status',c.subscription_status,'current_period_end',c.current_period_end),
 'entitlements', coalesce((select data from ents),'{}'::jsonb),
 'device_count',(select count(*) from public.devices d where d.user_id=auth.uid() and d.revoked_at is null)
)
from auth.users u left join public.profiles pr on pr.user_id=u.id cross join chosen c where u.id=auth.uid();
$$;
grant execute on function public.get_account_snapshot() to authenticated;

create or replace function public.register_device(
 p_device_id text,p_device_name text,p_platform text,p_extension_version text,p_agent_version text
) returns jsonb
language plpgsql security definer set search_path=public as $$
declare lim int; cnt int; rec public.devices;
begin
 if auth.uid() is null then raise exception 'AUTH_REQUIRED'; end if;
 select coalesce((public.get_account_snapshot()->'plan'->>'device_limit')::int,1) into lim;
 select count(*) into cnt from public.devices where user_id=auth.uid() and revoked_at is null and device_id<>p_device_id;
 if cnt >= lim and not exists(select 1 from public.devices where user_id=auth.uid() and device_id=p_device_id and revoked_at is null) then
   raise exception 'DEVICE_LIMIT_REACHED';
 end if;
 insert into public.devices(user_id,device_id,device_name,platform,extension_version,agent_version)
 values(auth.uid(),p_device_id,p_device_name,p_platform,p_extension_version,p_agent_version)
 on conflict(user_id,device_id) do update set device_name=excluded.device_name,platform=excluded.platform,extension_version=excluded.extension_version,agent_version=excluded.agent_version,last_seen_at=now(),revoked_at=null
 returning * into rec;
 return to_jsonb(rec);
end; $$;
grant execute on function public.register_device(text,text,text,text,text) to authenticated;
