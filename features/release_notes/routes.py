"""Release-notes API — feeds the in-app "What's New" panel."""

from flask import Blueprint, jsonify

from shared.release_notes import get_releases, latest_version

bp = Blueprint("release_notes", __name__)


@bp.route("/api/release-notes")
def api_release_notes():
    return jsonify({
        "releases": get_releases(),
        "latest_version": latest_version(),
    })
