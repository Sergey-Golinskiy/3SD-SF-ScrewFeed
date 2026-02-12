"""
Authentication module for ScrewDrive Web UI.
Handles user authentication, session management, and access control.
"""

import os
import bcrypt
import yaml
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from pathlib import Path


# Path to auth config
AUTH_CONFIG_PATH = Path(__file__).parent.parent / "config" / "auth.yaml"


def load_auth_config() -> dict:
    """Load authentication configuration from YAML file."""
    if not AUTH_CONFIG_PATH.exists():
        return {"users": {}, "secret_key": "default-secret-key", "session": {}, "available_tabs": []}

    with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_auth_config(config: dict) -> None:
    """Save authentication configuration to YAML file."""
    with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def get_secret_key() -> str:
    """Get secret key for session signing."""
    config = load_auth_config()
    return config.get("secret_key", "default-secret-key-change-me")


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def authenticate_user(username: str, password: str) -> dict | None:
    """
    Authenticate user by username and password.
    Returns user dict if successful, None otherwise.
    """
    config = load_auth_config()
    users = config.get("users", {})

    user = users.get(username)
    if not user:
        return None

    if verify_password(password, user.get("password_hash", "")):
        return {
            "username": username,
            "role": user.get("role", "user"),
            "allowed_tabs": user.get("allowed_tabs", [])
        }

    return None


def get_current_user() -> dict | None:
    """Get current logged in user from session."""
    if "user" in session:
        return session["user"]
    return None


def is_logged_in() -> bool:
    """Check if user is logged in."""
    return "user" in session


def login_user(user: dict) -> None:
    """Log in user by storing in session."""
    session["user"] = user
    session.permanent = True


def logout_user() -> None:
    """Log out user by clearing session."""
    session.pop("user", None)


def login_required(f):
    """Decorator to require login for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login_page"))

        if user.get("role") != "admin":
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Admin access required"}), 403
            return redirect(url_for("index"))

        return f(*args, **kwargs)
    return decorated_function


def has_tab_access(tab_name: str) -> bool:
    """Check if current user has access to a specific tab."""
    user = get_current_user()
    if not user:
        return False

    # Admin has access to everything
    if user.get("role") == "admin":
        return True

    allowed_tabs = user.get("allowed_tabs", [])
    return tab_name in allowed_tabs


def get_user_tabs() -> list:
    """Get list of tabs accessible by current user."""
    user = get_current_user()
    if not user:
        return []

    return user.get("allowed_tabs", [])


# User management functions (for admin panel)

def get_all_users() -> dict:
    """Get all users (without password hashes)."""
    config = load_auth_config()
    users = config.get("users", {})

    result = {}
    for username, data in users.items():
        result[username] = {
            "role": data.get("role", "user"),
            "allowed_tabs": data.get("allowed_tabs", [])
        }

    return result


def create_user(username: str, password: str, role: str = "user", allowed_tabs: list = None) -> bool:
    """Create a new user."""
    if not username or not password:
        return False

    config = load_auth_config()
    users = config.get("users", {})

    if username in users:
        return False  # User already exists

    users[username] = {
        "password_hash": hash_password(password),
        "role": role,
        "allowed_tabs": allowed_tabs or ["status"]
    }

    config["users"] = users
    save_auth_config(config)
    return True


def update_user(username: str, password: str = None, role: str = None, allowed_tabs: list = None) -> bool:
    """Update existing user."""
    config = load_auth_config()
    users = config.get("users", {})

    if username not in users:
        return False

    if password:
        users[username]["password_hash"] = hash_password(password)

    if role is not None:
        users[username]["role"] = role

    if allowed_tabs is not None:
        users[username]["allowed_tabs"] = allowed_tabs

    config["users"] = users
    save_auth_config(config)
    return True


def delete_user(username: str) -> bool:
    """Delete a user."""
    config = load_auth_config()
    users = config.get("users", {})

    if username not in users:
        return False

    # Don't allow deleting the last admin
    if users[username].get("role") == "admin":
        admin_count = sum(1 for u in users.values() if u.get("role") == "admin")
        if admin_count <= 1:
            return False

    del users[username]
    config["users"] = users
    save_auth_config(config)
    return True


def get_available_tabs() -> list:
    """Get list of all available tabs."""
    config = load_auth_config()
    return config.get("available_tabs", ["status", "control", "xy", "settings", "admin"])
