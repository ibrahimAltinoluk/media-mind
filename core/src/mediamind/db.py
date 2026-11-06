"""SQLite şeması ve migration'lar."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE assets (
        id              INTEGER PRIMARY KEY,
        blake3          TEXT NOT NULL UNIQUE,
        size            INTEGER NOT NULL,
        kind            TEXT NOT NULL,              -- photo | video
        mime            TEXT,
        width           INTEGER,
        height          INTEGER,
        duration_ms     INTEGER,
        taken_at        TEXT,                       -- yerel saat, ISO (YYYY-MM-DDTHH:MM:SS)
        taken_at_source TEXT,                       -- exif | quicktime | filename | mtime
        tz_offset       TEXT,                       -- +03:00 (biliniyorsa)
        lat             REAL,
        lon             REAL,
        alt             REAL,
        place_id        INTEGER REFERENCES places(id),
        camera_make     TEXT,
        camera_model    TEXT,
        lens            TEXT,
        orientation     INTEGER,
        video_codec     TEXT,
        bitrate         INTEGER,
        phash           TEXT,                       -- 64 bit, hex
        dhash           TEXT,
        blur_score      REAL,
        rating          INTEGER NOT NULL DEFAULT 0,
        favorite        INTEGER NOT NULL DEFAULT 0,
        analysis_state  TEXT NOT NULL DEFAULT '{}', -- {"meta":1,"geo":1,"thumbs":1,"errors":{...}}
        created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE INDEX assets_taken_at ON assets(taken_at);
    CREATE INDEX assets_phash ON assets(phash);

    CREATE TABLE files (
        id          INTEGER PRIMARY KEY,
        asset_id    INTEGER NOT NULL REFERENCES assets(id),
        rel_path    TEXT NOT NULL UNIQUE,           -- NFC, repo köküne göre
        fs_path     TEXT NOT NULL,                  -- diskteki gerçek yazım (NFD olabilir)
        size        INTEGER NOT NULL,
        mtime_ns    INTEGER NOT NULL,
        state       TEXT NOT NULL,                  -- present | missing | trashed
        tracked     INTEGER NOT NULL DEFAULT 0,
        first_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
        last_seen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE INDEX files_asset ON files(asset_id);
    CREATE INDEX files_state ON files(state, tracked);

    CREATE TABLE places (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL,
        admin1       TEXT,
        admin2       TEXT,
        country_code TEXT,
        UNIQUE(name, admin1, country_code)
    );

    CREATE TABLE tags (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'user',          -- object | scene | animal | user | album
        UNIQUE(name, kind)
    );
    CREATE TABLE asset_tags (
        asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        source     TEXT NOT NULL DEFAULT 'user',    -- model | user
        confidence REAL,
        model_ver  TEXT,
        PRIMARY KEY(asset_id, tag_id, source)
    );

    CREATE TABLE dup_groups (
        id         INTEGER PRIMARY KEY,
        kind       TEXT NOT NULL,                   -- exact | near | video
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE TABLE dup_members (
        group_id INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
        file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        role     TEXT NOT NULL,                     -- keep | delete | optional
        reason   TEXT,
        PRIMARY KEY(group_id, file_id)
    );

    CREATE TABLE plans (
        id         INTEGER PRIMARY KEY,
        kind       TEXT NOT NULL,                   -- trash | move | organize | dedup
        source     TEXT NOT NULL DEFAULT 'cli',     -- cli | ui | assistant | mcp:<istemci>
        status     TEXT NOT NULL DEFAULT 'pending', -- pending | running | applied | partial | rejected | undone | failed
        title      TEXT,
        summary    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
        applied_at TEXT,
        undone_at  TEXT
    );
    CREATE TABLE plan_items (
        id      INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        seq     INTEGER NOT NULL,
        op      TEXT NOT NULL,                      -- move | trash
        file_id INTEGER REFERENCES files(id) ON DELETE SET NULL, -- yan dosyalar için NULL
        src     TEXT NOT NULL,                      -- göreli yol
        dst     TEXT NOT NULL,                      -- göreli yol (.mediamind/trash/... olabilir)
        size    INTEGER NOT NULL DEFAULT 0,
        status  TEXT NOT NULL DEFAULT 'pending',    -- pending | running | done | failed | skipped | undone
        error   TEXT
    );
    CREATE INDEX plan_items_plan ON plan_items(plan_id, seq);

    CREATE TABLE jobs (
        id       INTEGER PRIMARY KEY,
        type     TEXT NOT NULL,
        asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
        priority INTEGER NOT NULL DEFAULT 0,
        status   TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        error    TEXT
    );
    """,
    # v2: içerik vektörleri, yüzler, kişiler
    """
    CREATE TABLE asset_vectors (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
        model    TEXT NOT NULL,
        vec      BLOB NOT NULL                       -- float32, L2 normalize
    );

    CREATE TABLE persons (
        id            INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        kind          TEXT NOT NULL DEFAULT 'human',
        cover_face_id INTEGER,
        hidden        INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE UNIQUE INDEX persons_name ON persons(name COLLATE NOCASE);

    CREATE TABLE faces (
        id                 INTEGER PRIMARY KEY,
        asset_id           INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,  -- 0..1, görünen yönde
        score              REAL NOT NULL,
        quality            REAL NOT NULL,
        vec                BLOB NOT NULL,            -- float32[512], L2 normalize
        cluster_id         INTEGER,                  -- adsız küme (en küçük yüz kimliği)
        person_id          INTEGER REFERENCES persons(id) ON DELETE SET NULL,
        assigned_by        TEXT,                     -- auto | user
        rejected_person_id INTEGER                   -- kullanıcı "bu o değil" dedi
    );
    CREATE INDEX faces_asset ON faces(asset_id);
    CREATE INDEX faces_person ON faces(person_id);
    CREATE INDEX faces_cluster ON faces(cluster_id);
    """,
    # v3: kullanıcı düzeltmeleri — yüz kümelemeden çıkarıldı ('removed') ya da yüz değil ('not_face')
    """
    ALTER TABLE faces ADD COLUMN excluded TEXT;
    """,
    # v4: kullanıcının elle eklediği kişi etiketleri (kutu çizerek ya da tüm fotoğrafa); vec boş olabilir
    """
    ALTER TABLE faces ADD COLUMN manual INTEGER NOT NULL DEFAULT 0;
    """,
    # v5: video sahneleri, örneklenen karelerin vektörleri, yüzlerin video içi zamanı
    """
    ALTER TABLE faces ADD COLUMN frame_ms INTEGER;
    CREATE TABLE video_scenes (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        idx      INTEGER NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms   INTEGER NOT NULL,
        key_ms   INTEGER NOT NULL,
        PRIMARY KEY(asset_id, idx)
    );
    CREATE TABLE video_frames (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        ms       INTEGER NOT NULL,
        vec      BLOB NOT NULL,
        PRIMARY KEY(asset_id, ms)
    );
    """,
    # v6: konuşma dökümü
    """
    CREATE TABLE transcripts (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        idx      INTEGER NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms   INTEGER NOT NULL,
        text     TEXT NOT NULL,
        lang     TEXT,
        PRIMARY KEY(asset_id, idx)
    );
    """,
    # v7: kişiye göre süzme hızlı olsun (büyük arşivlerde EXISTS alt sorgusu tüm kişi yüzlerini tarıyordu)
    """
    CREATE INDEX IF NOT EXISTS faces_asset_person ON faces(asset_id, person_id);
    CREATE INDEX IF NOT EXISTS files_asset_state ON files(asset_id, state, tracked);
    """,
    # v8: yer adlarında aksansız arama, fotoğraftaki yazı (OCR), görüntü parlaklığı (temizlik önerileri),
    #     kullanıcının tarih/konum düzeltmeleri, albümler
    """
    ALTER TABLE places ADD COLUMN name_fold TEXT;
    ALTER TABLE places ADD COLUMN admin1_fold TEXT;
    ALTER TABLE assets ADD COLUMN luma REAL;
    ALTER TABLE assets ADD COLUMN contrast REAL;
    CREATE TABLE asset_text (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
        text     TEXT NOT NULL,
        fold     TEXT NOT NULL
    );
    CREATE TABLE asset_edits (
        asset_id  INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
        taken_at  TEXT,              -- kullanıcının düzelttiği tarih (yoksa NULL)
        orig_taken_at TEXT,
        orig_taken_at_source TEXT,
        lat REAL, lon REAL,          -- kullanıcının verdiği konum (yoksa NULL)
        orig_lat REAL, orig_lon REAL, orig_place_id INTEGER,
        edited_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE TABLE albums (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        kind       TEXT NOT NULL DEFAULT 'manual',   -- manual | smart
        rules      TEXT,                              -- akıllı albüm: süzgeç (JSON)
        cover_asset_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
    );
    CREATE TABLE album_items (
        album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
        PRIMARY KEY (album_id, asset_id)
    );
    CREATE INDEX album_items_asset ON album_items(asset_id);
    CREATE TABLE cleanup_keep (       -- temizlik önerisinde "bunlar kalsın" denenler bir daha önerilmez
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        category TEXT NOT NULL,
        PRIMARY KEY (asset_id, category)
    );
    """,
    # v9: "bunlar farklı kişiler" denen kişi çiftleri birleştirme önerisinde bir daha çıkmaz
    """
    CREATE TABLE person_not_same (
        a INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
        b INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
        PRIMARY KEY (a, b)
    );
    """,
    # v10: evcil hayvanlar (faces tablosunda, tür adıyla; vektör 384 boyutlu DINOv2)
    """
    ALTER TABLE faces ADD COLUMN animal TEXT;
    CREATE INDEX faces_animal ON faces(animal);
    """,
    # v11: telefondan/karttan içe aktarılanlar (aynı dosya bir daha aktarılmasın)
    """
    CREATE TABLE device_imports (
        device   TEXT NOT NULL,
        key      TEXT NOT NULL,
        blake3   TEXT,
        rel_path TEXT,
        imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
        PRIMARY KEY (device, key)
    );
    """,
    # v12: model kalitesi değişirken eski ve yeni modelin vektörleri yan yana durabilsin
    #      (geçiş bitene kadar arama eski modelle çalışır)
    """
    CREATE TABLE asset_vectors_v12 (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        model    TEXT NOT NULL,
        vec      BLOB NOT NULL,
        PRIMARY KEY (asset_id, model)
    );
    INSERT INTO asset_vectors_v12(asset_id, model, vec) SELECT asset_id, model, vec FROM asset_vectors;
    DROP TABLE asset_vectors;
    ALTER TABLE asset_vectors_v12 RENAME TO asset_vectors;
    CREATE INDEX asset_vectors_model ON asset_vectors(model);
    CREATE TABLE video_frames_v12 (
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        model    TEXT NOT NULL DEFAULT 'siglip2-base-224-int8',
        ms       INTEGER NOT NULL,
        vec      BLOB NOT NULL,
        PRIMARY KEY (asset_id, model, ms)
    );
    INSERT INTO video_frames_v12(asset_id, ms, vec) SELECT asset_id, ms, vec FROM video_frames;
    DROP TABLE video_frames;
    ALTER TABLE video_frames_v12 RENAME TO video_frames;
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # İki süreç (uygulama + MCP) aynı anda ilk kez açarsa WAL'a geçiş anlık "kilitli" dönebilir: kısa bekleyip yeniden dene
    for attempt in range(20):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == 19:
                raise
            time.sleep(0.1)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Bu repo daha yeni bir MediaMind sürümüyle oluşturulmuş (şema {current} > {SCHEMA_VERSION})."
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with conn:
            conn.executescript("BEGIN;" + MIGRATIONS[version - 1] + ";")
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now','localtime'))",
                (version,),
            )
    return SCHEMA_VERSION
