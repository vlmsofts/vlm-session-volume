"""
app.py -- Flask app factory. Serves the query API + the dashboard UI.

Run locally:  python -m api.app          (port SESSIONVOL_PORT, default 5062
              -- NOT 5060/5061: Chrome blocks those as unsafe SIP ports)
Railway:      gunicorn 'api.app:create_app()'
"""

import os

from flask import Flask, render_template

from store.db import connect


def create_app(database_url: str = None) -> Flask:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(repo_root, 'ui', 'templates'),
        static_folder=os.path.join(repo_root, 'ui', 'static'),
    )
    # Jinja caches the compiled template at first render, so with this off a
    # long-lived server keeps serving the dashboard.html it read at startup and
    # edits to the file are invisible until the process restarts.
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # read_only=True -> autocommit. This connection lives for the whole
    # process and NEVER writes (12 read call sites in routes_query, zero
    # exec/execmany/commit). Without autocommit every SELECT would open a
    # transaction nothing ever closes, leaving the connection
    # `idle in transaction` holding AccessShareLock on the tables it read --
    # which blocked the 2026-08-24 side migration twice and prevents VACUUM
    # from reclaiming dead rows. See DEFECT_IDLE_IN_TRANSACTION.md.
    app.config['DB'] = connect(database_url, read_only=True)

    from api.routes_query import bp as query_bp
    app.register_blueprint(query_bp)

    @app.get('/')
    def dashboard():
        return render_template('dashboard.html')

    @app.get('/health')
    def health():
        return {'ok': True}

    return app


if __name__ == '__main__':
    port = int(os.environ.get('SESSIONVOL_PORT', '5062'))
    create_app().run(host='127.0.0.1', port=port, debug=False)
