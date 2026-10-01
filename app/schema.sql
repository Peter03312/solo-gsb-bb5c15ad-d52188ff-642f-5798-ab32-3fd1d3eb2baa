-- 裁切排样应用的持久化结构（幂等：可重复执行）。
-- 单行工单（job）+ 成品种类 + 最近一次方案。

CREATE TABLE IF NOT EXISTS job (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    paper_l         INTEGER NOT NULL,
    paper_w         INTEGER NOT NULL,
    kerf            INTEGER NOT NULL,
    max_sheets      INTEGER NOT NULL,
    max_states      INTEGER NOT NULL,
    input_hash      TEXT NOT NULL,          -- 当前输入版本指纹
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position    INTEGER NOT NULL,           -- 在输入中的顺序，即成品类型 id
    name        TEXT NOT NULL,
    l           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    qty         INTEGER NOT NULL,
    rotatable   INTEGER NOT NULL CHECK (rotatable IN (0, 1))
);

CREATE TABLE IF NOT EXISTS solution (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    status          TEXT NOT NULL,          -- found / infeasible / budget
    input_hash      TEXT NOT NULL,          -- 方案所对应的输入版本
    result_json     TEXT NOT NULL,          -- solver 完整结果
    states_searched INTEGER NOT NULL,
    sheets_used     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
