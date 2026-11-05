PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('employee','admin')), password_hash TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  csrf TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_versions (
  user_id TEXT PRIMARY KEY REFERENCES users(id), version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS credential_intents (
  user_id TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, password_hash TEXT NOT NULL,
  PRIMARY KEY(user_id,key)
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, version_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  category TEXT NOT NULL, label TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES users(id),
  scope TEXT NOT NULL DEFAULT 'private', shares_json TEXT NOT NULL DEFAULT '[]',
  publication TEXT NOT NULL DEFAULT 'draft', sections_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_categories (
  source_id TEXT PRIMARY KEY REFERENCES sources(id), category_id TEXT NOT NULL REFERENCES categories(id)
);
CREATE TABLE IF NOT EXISTS source_families (
  id TEXT PRIMARY KEY, current_source_id TEXT REFERENCES sources(id),
  version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_versions (
  source_id TEXT PRIMARY KEY REFERENCES sources(id), family_id TEXT NOT NULL REFERENCES source_families(id),
  number INTEGER NOT NULL CHECK(number >= 1), UNIQUE(family_id,number)
);
CREATE TABLE IF NOT EXISTS publication_requests (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  admin_id TEXT NOT NULL REFERENCES users(id), source_version INTEGER NOT NULL,
  scope TEXT NOT NULL, shares_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_jobs (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL, file_sha256 TEXT NOT NULL, file_size INTEGER NOT NULL,
  file_bytes BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  sections_json TEXT NOT NULL DEFAULT '[]', warnings_json TEXT NOT NULL DEFAULT '[]',
  characters INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
  source_id TEXT REFERENCES sources(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id,file_sha256)
);
CREATE TABLE IF NOT EXISTS import_targets (
  job_id TEXT PRIMARY KEY REFERENCES import_jobs(id) ON DELETE CASCADE,
  parent_source_id TEXT NOT NULL REFERENCES sources(id), base_family_version INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_publication
  ON publication_requests(source_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS publication_context (
  request_id TEXT PRIMARY KEY REFERENCES publication_requests(id), family_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
  template TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_sharing (
  artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),
  scope TEXT NOT NULL DEFAULT 'private' CHECK(scope IN ('private','selected','internal')),
  shares_json TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
  artifact_id TEXT NOT NULL REFERENCES artifacts(id), revision INTEGER NOT NULL,
  title TEXT NOT NULL, content_json TEXT NOT NULL, citations_json TEXT NOT NULL,
  author_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  PRIMARY KEY(artifact_id,revision)
);
CREATE TABLE IF NOT EXISTS artifact_working_copies (
  artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),
  version INTEGER NOT NULL DEFAULT 0, base_revision INTEGER NOT NULL,
  payload_json TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, current_version INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS template_versions (
  id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES templates(id),
  number INTEGER NOT NULL, name TEXT NOT NULL, fields_json TEXT NOT NULL,
  sections_json TEXT NOT NULL, created_at TEXT NOT NULL,
  created_by TEXT REFERENCES users(id), UNIQUE(template_id,number)
);
CREATE TABLE IF NOT EXISTS artifact_templates (
  artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id),
  template_version_id TEXT NOT NULL REFERENCES template_versions(id)
);
CREATE TABLE IF NOT EXISTS revision_fields (
  artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, values_json TEXT NOT NULL,
  PRIMARY KEY(artifact_id,revision),
  FOREIGN KEY(artifact_id,revision) REFERENCES revisions(artifact_id,revision)
);
CREATE TABLE IF NOT EXISTS writing_suggestions (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  owner_id TEXT NOT NULL REFERENCES users(id), base_revision INTEGER NOT NULL,
  target_block_id TEXT NOT NULL, target_hash TEXT NOT NULL, source_ref_json TEXT NOT NULL,
  operation TEXT NOT NULL CHECK(operation IN ('insert_after','replace')),
  status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','superseded','applied','undone','canceled')), expires REAL NOT NULL,
  applied_revision INTEGER, undo_revision INTEGER, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_suggestions_by_artifact ON writing_suggestions(artifact_id,status,applied_revision);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  revision INTEGER NOT NULL, reviewer_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, decided_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_review
  ON reviews(artifact_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  revision INTEGER NOT NULL,
  author_id TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
  revision INTEGER NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
  content BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_by_owner ON conversations(user_id,updated_at);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), question TEXT NOT NULL,
  refs_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
  message_id TEXT PRIMARY KEY REFERENCES messages(id),
  conversation_id TEXT NOT NULL REFERENCES conversations(id), sequence INTEGER NOT NULL,
  reply_type TEXT NOT NULL CHECK(reply_type IN ('evidence_excerpt','needs_info','no_evidence')),
  clarification_for TEXT REFERENCES messages(id), clarification_prompt TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'all', UNIQUE(conversation_id,sequence)
);
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY, message_id TEXT UNIQUE NOT NULL REFERENCES messages(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')),
  reason TEXT NOT NULL CHECK(reason IN ('useful','irrelevant','missing','outdated','locator','other')),
  note TEXT NOT NULL DEFAULT '', answer_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','closed')),
  resolution TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_previews (
  id TEXT PRIMARY KEY, creator_id TEXT NOT NULL REFERENCES users(id), payload_json TEXT NOT NULL,
  expires REAL NOT NULL, task_id TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, creator_id TEXT NOT NULL REFERENCES users(id),
  assignee_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL, note TEXT NOT NULL,
  due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
  user_id TEXT NOT NULL, key TEXT NOT NULL, route TEXT NOT NULL, payload_hash TEXT NOT NULL,
  result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,key)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL, action TEXT NOT NULL,
  object_id TEXT NOT NULL, created_at TEXT NOT NULL
);
