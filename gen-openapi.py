#!/usr/bin/env python
"""
Generates openapi.json file in the web/src/lib directory based on the FastAPI app's OpenAPI specs.
This allows the frontend to have access to the API schema for things like type generation and API client generation.
"""
from io import StringIO
from pathlib import Path
from json import dumps
import sys
_stdout = sys.stdout

sys.stdout = StringIO()
from app.main import app
sys.stdout = _stdout

openapi_specs = app.openapi()
(Path(__file__).parent / 'web' / 'src' / 'lib' / 'openapi.json').write_text(dumps(openapi_specs))
