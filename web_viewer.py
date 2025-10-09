# web_viewer.py
import csv
import io
import os
from urllib.parse import urlencode

from flask import Flask, Response, abort, jsonify, render_template_string, request
from jinja2 import DictLoader
from psycopg_pool import ConnectionPool

# ---------- Config (env) ----------
PGHOST = os.getenv("PGHOST", "192.168.0.8")
PGPORT = int(os.getenv("PGPORT", "5433"))
PGDATABASE = os.getenv("PGDATABASE", "ctip")  # <- suggest using ctip
PGUSER = os.getenv("PGUSER", "appuser")
PGPASSWORD = os.getenv("PGPASSWORD", "change_me")
PGSCHEMA = os.getenv("PGSCHEMA", "ctip")  # app-only variable (NOT passed to libpq)
PGSSLMODE = os.getenv("PGSSLMODE", "disable")
POOL_MIN = int(os.getenv("POOL_MIN", "1"))
POOL_MAX = int(os.getenv("POOL_MAX", "10"))

# IMPORTANT: do not put PGSCHEMA into CONNINFO
CONNINFO = (
    f"host={PGHOST} port={PGPORT} dbname={PGDATABASE} "
    f"user={PGUSER} password={PGPASSWORD} sslmode={PGSSLMODE}"
)

# Create pool (autocommit for read-only and simple inserts)
pool = ConnectionPool(
    conninfo=CONNINFO, min_size=POOL_MIN, max_size=POOL_MAX, kwargs={"autocommit": True}
)

app = Flask(__name__)

# ---------- Templates (inline) ----------
BASE_HTML = """
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{{ title or "PG Web Viewer" }}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  body { padding: 20px; }
  .table-wrap { overflow-x: auto; }
  th a { text-decoration: none; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
</style>
</head><body>
<div class="container">
  <nav class="mb-4 d-flex gap-2">
    <a class="btn btn-outline-primary" href="{{ url_for('home') }}">Home</a>
    <a class="btn btn-outline-secondary" href="{{ url_for('tables') }}">Tables</a>
  </nav>
  {% block content %}{% endblock %}
</div>
</body></html>
"""

HOME_HTML = """
{% extends "base" %}{% block content %}
<div class="card shadow-sm"><div class="card-body">
  <h1 class="h4 mb-3">PostgreSQL Web Viewer</h1>
  <p class="mb-1"><b>Host:</b> {{ pg_host }}:{{ pg_port }}</p>
  <p class="mb-1"><b>Database:</b> {{ pg_db }}</p>
  <p class="mb-3"><b>User:</b> {{ pg_user }}</p>
  <a class="btn btn-primary" href="{{ url_for('tables') }}">List tables in schema "{{ pg_schema }}"</a>
</div></div>
{% endblock %}
"""

TABLES_HTML = """
{% extends "base" %}{% block content %}
<h2 class="h5 mb-3">Tables in schema "{{ pg_schema }}"</h2>
{% if tables %}
  <div class="list-group">
  {% for t in tables %}
    <a class="list-group-item list-group-item-action" href="{{ url_for('table_view', table=t) }}">{{ t }}</a>
  {% endfor %}
  </div>
{% else %}
  <div class="alert alert-warning">No tables found.</div>
{% endif %}
{% endblock %}
"""

TABLE_HTML = """
{% extends "base" %}{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <h2 class="h5 mb-0">Table: <span class="mono">{{ table }}</span></h2>
  <div class="d-flex gap-2">
    <a class="btn btn-outline-secondary" href="{{ url_for('tables') }}">Back</a>
    <a class="btn btn-success" href="{{ url_with_params(url_for('table_csv', table=table), q=q, sort=sort, order=order) }}">Export CSV</a>
  </div>
</div>

<form class="row row-cols-lg-auto g-2 align-items-end mb-3" method="get">
  <div class="col-12">
    <label class="form-label">Search</label>
    <input class="form-control" name="q" value="{{ q or '' }}" placeholder="type to search">
  </div>
  <div class="col-12">
    <label class="form-label">Page size</label>
    <input class="form-control" type="number" name="page_size" value="{{ page_size }}" min="5" max="200">
  </div>
  <div class="col-12">
    <button class="btn btn-primary" type="submit">Apply</button>
  </div>
</form>

<div class="table-wrap">
<table class="table table-sm table-hover align-middle">
  <thead><tr>
    {% for c in columns %}
      {% set is_current = (sort == c.name) %}
      {% set next_order = 'desc' if (is_current and order != 'desc') else 'asc' %}
      <th><a href="{{ url_with_params(url_for('table_view', table=table), q=q, page=page, page_size=page_size, sort=c.name, order=next_order) }}">
        {{ c.name }}{% if is_current %} {{ '▼' if order=='desc' else '▲' }}{% endif %}
      </a></th>
    {% endfor %}
  </tr></thead>
  <tbody>
    {% for r in rows %}
      <tr>
        {% for c in columns %}<td>{{ r.get(c.name) }}</td>{% endfor %}
      </tr>
    {% endfor %}
    {% if rows|length == 0 %}
      <tr><td colspan="{{ columns|length }}"><em>No rows</em></td></tr>
    {% endif %}
  </tbody>
</table>
</div>

{% set total_pages = (total // page_size) + (1 if total % page_size else 0) %}
<div class="d-flex justify-content-between align-items-center">
  <div>
    <span class="badge text-bg-light">Total: {{ total }}</span>
    <span class="badge text-bg-light">Page: {{ page }}/{{ total_pages if total_pages else 1 }}</span>
  </div>
  <div class="btn-group">
    <a class="btn btn-outline-secondary {% if page<=1 %}disabled{% endif %}" href="{{ url_with_params(url_for('table_view', table=table), q=q, page=page-1 if page>1 else 1, page_size=page_size, sort=sort, order=order) }}">Prev</a>
    <a class="btn btn-outline-secondary {% if page>=total_pages %}disabled{% endif %}" href="{{ url_with_params(url_for('table_view', table=table), q=q, page=page+1 if page<total_pages else total_pages, page_size=page_size, sort=sort, order=order) }}">Next</a>
  </div>
</div>
{% endblock %}
"""

# Register base template in memory
app.jinja_loader = DictLoader({"base": BASE_HTML})


# ---------- Helpers ----------
def url_with_params(base, **kwargs):
    clean = {k: v for k, v in kwargs.items() if v not in [None, "", 0]}
    return f"{base}?{urlencode(clean)}" if clean else base


def list_tables(schema: str):
    sql = """
      select table_name
      from information_schema.tables
      where table_schema = %s and table_type='BASE TABLE'
      order by table_name
    """
    with pool.connection() as c, c.cursor() as cur:
        cur.execute(sql, (schema,))
        return [r[0] for r in cur.fetchall()]


def list_columns(table: str, schema: str):
    sql = """
      select column_name, data_type
      from information_schema.columns
      where table_schema=%s and table_name=%s
      order by ordinal_position
    """
    with pool.connection() as c, c.cursor() as cur:
        cur.execute(sql, (schema, table))
        return [{"name": r[0], "type": r[1]} for r in cur.fetchall()]


def text_like_columns(columns):
    text_types = {"character varying", "text", "character", "citext", "uuid"}
    return [c["name"] for c in columns if c["type"] in text_types]


def fetch_rows(table: str, schema: str, page=1, page_size=20, q=None, sort=None, order="asc"):
    cols = list_columns(table, schema)
    if not cols:
        return cols, [], 0

    params = []
    where_sql = ""
    tcols = text_like_columns(cols)
    if q and tcols:
        like = " OR ".join([f'"{c}" ILIKE %s' for c in tcols])
        where_sql = f"WHERE ({like})"
        params.extend([f"%{q}%"] * len(tcols))

    count_sql = f'SELECT count(*) FROM "{schema}"."{table}" {where_sql}'
    with pool.connection() as c, c.cursor() as cur:
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

    order_sql = ""
    names = [c["name"] for c in cols]
    if sort in names:
        direction = "DESC" if (order or "").lower() == "desc" else "ASC"
        order_sql = f'ORDER BY "{sort}" {direction}'

    page = max(1, int(page or 1))
    page_size = min(max(int(page_size or 20), 5), 200)
    offset = (page - 1) * page_size

    data_sql = f'SELECT * FROM "{schema}"."{table}" {where_sql} {order_sql} LIMIT %s OFFSET %s'
    with pool.connection() as c, c.cursor() as cur:
        cur.execute(data_sql, (*params, page_size, offset))
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]

    dict_rows = [dict(zip(colnames, r, strict=False)) for r in rows]
    return cols, dict_rows, total


# ---------- Routes ----------
@app.route("/health")
def health():
    try:
        with pool.connection() as c, c.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/")
def home():
    return render_template_string(
        HOME_HTML,
        title="PG Web Viewer",
        pg_host=PGHOST,
        pg_port=PGPORT,
        pg_db=PGDATABASE,
        pg_user=PGUSER,
        pg_schema=PGSCHEMA,
    )


@app.route("/tables")
def tables():
    tbls = list_tables(PGSCHEMA)
    return render_template_string(TABLES_HTML, tables=tbls, pg_schema=PGSCHEMA)


@app.route("/table/<table>")
def table_view(table):
    tbls = list_tables(PGSCHEMA)
    if table not in tbls:
        abort(404, description="Table not found")

    q = request.args.get("q") or None
    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 20)
    sort = request.args.get("sort") or None
    order = request.args.get("order") or "asc"

    columns, rows, total = fetch_rows(
        table=table,
        schema=PGSCHEMA,
        page=page,
        page_size=min(max(page_size, 5), 200),
        q=q,
        sort=sort,
        order=order,
    )

    return render_template_string(
        TABLE_HTML,
        title=f"Table: {table}",
        table=table,
        columns=columns,
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
        q=q,
        sort=sort,
        order=order,
        url_with_params=url_with_params,
    )


@app.route("/table/<table>/csv")
def table_csv(table):
    tbls = list_tables(PGSCHEMA)
    if table not in tbls:
        abort(404, description="Table not found")

    q = request.args.get("q") or None
    sort = request.args.get("sort") or None
    order = request.args.get("order") or "asc"

    columns = list_columns(table, PGSCHEMA)
    params = []
    where_sql = ""
    tcols = text_like_columns(columns)
    if q and tcols:
        like = " OR ".join([f'"{c}" ILIKE %s' for c in tcols])
        where_sql = f"WHERE ({like})"
        params.extend([f"%{q}%"] * len(tcols))

    order_sql = ""
    names = [c["name"] for c in columns]
    if sort in names:
        direction = "DESC" if (order or "").lower() == "desc" else "ASC"
        order_sql = f'ORDER BY "{sort}" {direction}'

    sql = f'SELECT * FROM "{PGSCHEMA}"."{table}" {where_sql} {order_sql}'
    with pool.connection() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(colnames)
    for r in rows:
        writer.writerow(r)
    data = buf.getvalue().encode("utf-8-sig")

    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


# ---------- Entry ----------
if __name__ == "__main__":
    # For production behind IIS/NGINX use a proper WSGI/ASGI server.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
