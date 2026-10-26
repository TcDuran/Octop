-- Match upstream v24 while retaining SQLite knowledge indexes.
ALTER TABLE public.user_group ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE public.user_group ADD COLUMN avatar_icon TEXT;
ALTER TABLE public.knowledge_bases ADD COLUMN icon_url TEXT NOT NULL DEFAULT '';
ALTER TABLE public.bridge_connections ADD COLUMN icon_url TEXT NOT NULL DEFAULT '';
ALTER TABLE public.bridge_connections ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;

UPDATE public._schema_version SET version = 24;
