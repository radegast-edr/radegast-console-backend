Radegast EDR — Backend
=======================

Radegast EDR is an Endpoint Detection and Response (EDR) platform designed for small teams and independent labs. The platform uses age end-to-end encryption (E2EE) to keep log data private. The server stores only encrypted data that the server cannot read.

The platform uses a built-in SQLite database. You do not need to install an external database server.

The backend uses FastAPI and SQLAlchemy. It manages device authorization, configuration packs, encrypted log storage, alert statuses, and user sessions.

Features
--------

- **Device Management**: Enroll agent devices, assign devices to groups, and generate authorization tokens.
- **Configuration Packs**: Store and distribute detection policies and versions.
- **End-to-End Encrypted Log Storage**: The agent encrypts logs with age before transmission. The server stores only encrypted data.
- **Team Collaboration**: Create teams, configure group permissions, and receive email notifications for critical events.
- **Zero-Trust Architecture**: The client encrypts all data locally. The server never accesses private keys or decrypted logs.
- **Self-Contained Deployment**: The built-in SQLite database requires no external database server.
- **Agent Distribution**: Distribute the Rustinel sensor and provide installation commands for Linux, Windows, and macOS.

Deployment
----------

To run Radegast EDR in production, use the container image.

Quick Start
~~~~~~~~~~~

.. code-block:: bash

   # Start with podman-compose (reads podman-compose.yaml)
   podman-compose up -d

   # Or start with podman or docker directly
   podman run -d \
     --name radegast-edr \
     -p 8000:8000 \
     -e RADEGAST_SECRET_KEY=<your-secret> \
     -e RADEGAST_BASE_URL=https://your.domain \
     -e RADEGAST_CORS_ORIGINS=https://your.domain \
     -v radegast_db:/app/data/db \
     -v radegast_uploads:/app/data/uploads \
     -v radegast_releases:/app/data/releases \
     docker.io/radegastedr/console:latest

For more details, refer to the `Deployment Guide <https://github.com/radegast-edr/radegast-console-backend#deployment-podman--docker>`_.

Local Development
-----------------

Prerequisites
~~~~~~~~~~~~~

- Python 3.11 or later
- ``uv`` (recommended) or standard ``pip``

Installation
~~~~~~~~~~~~

1. Install the project dependencies:

   .. code-block:: bash

      uv sync

2. Install the development tools:

   .. code-block:: bash

      uv sync --dev

Start the Backend
~~~~~~~~~~~~~~~~~

Start the development server with automatic reload:

.. code-block:: bash

   uv run uvicorn app.main:app --reload --port 8000

The server listens on http://localhost:8000. Interactive Swagger documentation is available at http://localhost:8000/docs.

Configuration
-------------

Environment variables with the ``RADEGAST_`` prefix control all settings. The settings are defined in ``app/config.py``.

Refer to the `Configuration Section in README <https://github.com/radegast-edr/radegast-console-backend#configuration>`_ for the complete list of options.
