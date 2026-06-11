Radegast EDR — Backend
=======================

Radegast EDR is a lightweight, privacy-focused Endpoint Detection and Response platform **perfect for smaller teams, home labbers, and families**. With complete end-to-end encryption (E2EE) using age encryption, your log data remains private and secure — even from the server itself. **No custom infrastructure is required**: the built-in SQLite database and self-contained deployment make it easy to get started without complex setup. You don't need to host any custom infrastructure if you don't want to.

Built with FastAPI and SQLAlchemy, the backend handles device authorization, user configuration packs, encrypted log storage, alert status tracking, and key/session management.

Features
--------

- **Device Management**: Create and enroll EDR agent devices, assign them to groups, and generate secure authorization tokens
- **Configuration Packs**: Store and distribute YAML/binary endpoint detection policies and versions
- **End-to-End Encrypted Log Storage**: All logs are encrypted on the device using ``age`` before transmission; the server stores only encrypted data it cannot read
- **Team Collaboration**: Create teams, manage device group permissions, and receive email notifications for critical events
- **Zero-Trust Architecture**: All data is encrypted client-side; the server never has access to your private keys or decrypted log contents
- **Self-Contained Deployment**: Built-in SQLite database means no external database server required
- **Agent Distribution**: Serve the Rustinel eBPF sensor and provide single-command installation for Linux and Windows

Deployment
----------

The recommended way to run Radegast EDR in production is via the published container image.

Quick Start
~~~~~~~~~~~

.. code-block:: bash

   # Pull and start with podman-compose (reads podman-compose.yaml)
   podman-compose up -d

   # Or with plain podman / docker
   podman run -d \\
     --name radegast-edr \\
     -p 8000:8000 \\
     -e RADEGAST_SECRET_KEY=<your-secret> \\
     -e RADEGAST_BASE_URL=https://your.domain \\
     -e RADEGAST_CORS_ORIGINS=https://your.domain \\
     -v radegast_db:/app/data/db \\
     -v radegast_uploads:/app/data/uploads \\
     -v radegast_releases:/app/data/releases \\
     docker.io/radegastedr/console:latest

For more details, see the `Deployment Guide <https://github.com/radegast-edr/radegast-console-backend#deployment-podman--docker>`_.

Local Development
------------------

Prerequisites
~~~~~~~~~~~~~

- Python 3.11+
- ``uv`` (recommended) or standard ``pip``

Installation
~~~~~~~~~~~~~

1. Install project dependencies:

   .. code-block:: bash

      uv sync

2. Install dev tools (test runner etc.):

   .. code-block:: bash

      uv sync --dev

Running the Backend
~~~~~~~~~~~~~~~~~~~

Start the development server with hot-reload:

.. code-block:: bash

   uv run uvicorn app.main:app --reload --port 8000

The server runs on http://localhost:8000. Interactive Swagger docs are available at http://localhost:8000/docs.

Configuration
-------------

All settings are controlled via environment variables prefixed with ``RADEGAST_`` (defined in ``app/config.py``).

See the `Configuration Section in README <https://github.com/radegast-edr/radegast-console-backend#configuration>`_ for the full list of configuration options.
