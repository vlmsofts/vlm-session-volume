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
    app.config['DB'] = connect(database_url)

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
