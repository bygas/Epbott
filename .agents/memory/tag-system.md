---
name: Tag system migration
description: How the Elited VIP bot's category system was replaced with a many-to-many tag system — schema, API endpoints, bot callbacks, and frontend pages.
---

# Tag system migration — Elited VIP

## Schema (added tables)
- `tags (id SERIAL PK, slug TEXT UNIQUE, label TEXT, emoji TEXT)`
- `video_tags (video_id INT FK videos.id, tag_id INT FK tags.id, PRIMARY KEY (video_id, tag_id))`
- `videos.category` column kept (legacy, unused in new flows)
- `pending_video_uploads.category` repurposed to store comma-separated tag IDs during bot upload

## Key API endpoints added/changed
- `GET /api/tags` — all tags with video count
- `POST /api/tags` — create tag (slug, label, emoji)
- `PUT /api/tags/<id>` — update label/emoji
- `DELETE /api/tags/<id>` — cascades video_tags
- `POST /api/videos/<id>/set-tags` — bulk replace a video's tags, triggers broadcast
- `GET /api/videos/untagged` — LEFT JOIN video_tags WHERE NULL
- `GET /api/user/videos-by-tag?tag=<slug>` — videos for a tag
- `GET /api/user/home` — now returns `recent_videos` (10 newest), `top_videos` (10 most viewed), `tags` list

## Bot callbacks
- `menu_tags:<page>` — paginated tag list
- `tagvids:<slug>:<page>` — videos in a tag
- All old `menu_cats:*` / `rootcat:*` / `subcat:*` references updated to tags

## pending_video_uploads flow
- Admin panel writes tag IDs as comma-separated string to `pending_video_uploads.category`
- `handle_video` in bot reads this string, inserts into `video_tags` after inserting the video
- `/yukle` bot command updated: no longer requires a category slug, just a title; tags assigned via admin panel

**Why:** `pending_video_uploads.category` column reused to avoid a schema migration; clearly a temp store, cleared after video insert.

## Frontend
- `app.html` — home shows En Son Eklenenler + En Çok İzlenenler sections; bottom nav Etiketler tab
- `admin.html` — Etiketler tab with full CRUD; video upload uses multi-select tag checkboxes; channel tab uses untagged endpoint + set-tags

## Known limitation
- Admin endpoints have no server-side auth token (same as pre-existing pattern); follow-up task #4 tracks adding X-Admin-Token protection.
