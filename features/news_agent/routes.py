"""News agent API — recent feed, trending stocks/sectors, admin refresh."""

from flask import Blueprint, jsonify, request

from decorators import login_required, admin_required

bp = Blueprint("news_agent", __name__)


@bp.route("/api/news/feed")
@login_required
def api_news_feed():
    """Recent ingested news/Reddit items.

    Query params: limit (default 50, max 200), ticker, category
    (market|stocks|sector|reddit|crypto|macro), source, hours.
    """
    from features.news_agent.agent import recent
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    hours = request.args.get("hours")
    items = recent(
        limit=limit,
        ticker=request.args.get("ticker"),
        category=request.args.get("category"),
        source=request.args.get("source"),
        hours=int(hours) if hours and hours.isdigit() else None,
    )
    return jsonify({"items": items, "count": len(items)})


@bp.route("/api/news/trending")
@login_required
def api_news_trending():
    """Trending stocks + sectors derived from the news/Reddit feed.
    Query params: hours (default 24, max 168), limit (default 10)."""
    from features.news_agent.agent import trending
    try:
        hours = min(168, max(1, int(request.args.get("hours", 24))))
        limit = min(30, max(1, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        hours, limit = 24, 10
    return jsonify(trending(hours=hours, limit=limit))


@bp.route("/api/news/refresh", methods=["POST"])
@admin_required
def api_news_refresh():
    """Force an immediate feed sweep (admin)."""
    from features.news_agent.agent import poll_feeds
    try:
        n = poll_feeds()
        return jsonify({"ok": True, "inserted": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
