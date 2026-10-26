-- Upgrade the retained openGauss baseline to upstream schema v23.
-- The fork baseline predates upstream v22's skill-package visibility column.
ALTER TABLE public.skill_packages ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;

CREATE SEQUENCE public.user_group_id_seq START WITH 1 INCREMENT BY 1
    MINVALUE 1 MAXVALUE 9223372036854775807 CACHE 1;
CREATE TABLE public.user_group (
    id BIGINT DEFAULT nextval('public.user_group_id_seq'::regclass) PRIMARY KEY,
    user_group_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
) WITH (storage_type=ASTORE);
ALTER SEQUENCE public.user_group_id_seq OWNED BY public.user_group.id;

CREATE TABLE public.user_group_member (
    user_group_id TEXT NOT NULL REFERENCES public.user_group(user_group_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL,
    PRIMARY KEY (user_group_id, user_id)
) WITH (storage_type=ASTORE);
CREATE INDEX idx_user_group_member_user ON public.user_group_member(user_id);

CREATE TABLE public.resource_group_grant (
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    user_group_id TEXT NOT NULL REFERENCES public.user_group(user_group_id) ON DELETE CASCADE,
    PRIMARY KEY (resource_type, resource_key, user_group_id)
) WITH (storage_type=ASTORE);
CREATE INDEX idx_resource_group_grant_group ON public.resource_group_grant(user_group_id);

UPDATE public._schema_version SET version = 23;
