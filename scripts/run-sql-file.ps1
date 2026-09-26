param(
    [Parameter(Mandatory = $true)]
    [string]$SqlFile
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$resolvedSqlFile = Resolve-Path $SqlFile

if (-not (Test-Path $python)) {
    throw "Backend environment missing. Run .\scripts\setup.ps1 first."
}

Push-Location $backend
try {
    $env:PAYMENT_OBSERVABILITY_SQL_FILE = $resolvedSqlFile.Path
    & $python -c @"
from pathlib import Path
import os

from app.config import get_settings
from app.services.sqlserver_replay_provider import SqlServerReplayProvider

sql_file = Path(os.environ["PAYMENT_OBSERVABILITY_SQL_FILE"])
sql = sql_file.read_text(encoding="utf-8")

provider = SqlServerReplayProvider(get_settings())
connection = provider._open_connection()
cursor = connection.cursor()
try:
    cursor.execute(sql)
    result_sets = []
    while True:
        if cursor.description:
            columns = [column[0] for column in cursor.description]
            result_sets.append([dict(zip(columns, row, strict=False)) for row in cursor.fetchall()])
        if not cursor.nextset():
            break
    if result_sets:
        print(result_sets[-1])
finally:
    cursor.close()
    connection.close()
"@
}
finally {
    Remove-Item Env:PAYMENT_OBSERVABILITY_SQL_FILE -ErrorAction SilentlyContinue
    Pop-Location
}
