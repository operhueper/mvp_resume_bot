-- Resume Bot tables (prefix rb_ to avoid conflicts with existing hh-bot tables)

CREATE TABLE IF NOT EXISTS rb_users (
    id BIGINT PRIMARY KEY,  -- Telegram ID as primary key
    telegram_username TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    current_stage TEXT DEFAULT 'onboarding',
    -- stages: 'onboarding', 'interview', 'draft', 'exported'
    interview_state JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS rb_candidate_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES rb_users(id) ON DELETE CASCADE,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    city TEXT,
    desired_position TEXT,
    summary TEXT,
    raw_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rb_work_experiences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES rb_candidate_profiles(id) ON DELETE CASCADE,
    company TEXT,
    position TEXT,
    start_date TEXT,
    end_date TEXT,
    is_current BOOLEAN DEFAULT FALSE,
    responsibilities TEXT[] DEFAULT '{}',
    achievements JSONB DEFAULT '[]'::jsonb,
    order_index INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rb_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES rb_candidate_profiles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'hard'
);

CREATE TABLE IF NOT EXISTS rb_education (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES rb_candidate_profiles(id) ON DELETE CASCADE,
    institution TEXT,
    degree TEXT,
    field TEXT,
    year TEXT
);

CREATE TABLE IF NOT EXISTS rb_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES rb_users(id) ON DELETE CASCADE,
    profile_id UUID REFERENCES rb_candidate_profiles(id),
    title TEXT,
    content TEXT,
    version INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rb_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT REFERENCES rb_users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_rb_users_id ON rb_users(id);
CREATE INDEX IF NOT EXISTS idx_rb_events_user_id ON rb_events(user_id);
CREATE INDEX IF NOT EXISTS idx_rb_events_type ON rb_events(event_type);
CREATE INDEX IF NOT EXISTS idx_rb_events_created ON rb_events(created_at);
CREATE INDEX IF NOT EXISTS idx_rb_resumes_user_id ON rb_resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_rb_profiles_user_id ON rb_candidate_profiles(user_id);
