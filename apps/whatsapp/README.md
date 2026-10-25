# Ansari WhatsApp Service

This service handles WhatsApp integration for the Ansari backend, providing a dedicated service for processing WhatsApp messages and communicating with the main Ansari backend.

## Overview

The Ansari WhatsApp service is designed as a separate microservice that:

1. Receives webhook events from the WhatsApp Business API
2. Processes incoming messages
3. Communicates with the main Ansari backend API
4. Sends responses back to WhatsApp users

By separating the WhatsApp functionality into its own service, we can:
- Scale the WhatsApp service independently
- Simplify the main Ansari backend codebase
- Make deployments and updates easier
- Improve testing and maintenance

## Setup

### Prerequisites

- Python 3.8 or higher
- Access to the WhatsApp Business API
- A running instance of the Ansari backend API

### Installation

1. Clone the repository:
   ```
   git clone <repository-url>
   cd ansari-whatsapp
   ```

2. Run the setup script:
   - On Linux/Mac:
     ```
     ./setup.sh
     ```
   - On Windows:
     ```
     setup.bat
     ```

3. Edit the `.env` file with your configuration settings:
   ```
   # Update these with your values
   META_BUSINESS_PHONE_NUMBER_ID=your_phone_number_id
   META_ACCESS_TOKEN_FROM_SYS_USER=your_access_token
   META_WEBHOOK_VERIFY_TOKEN=your_verify_token
   BACKEND_SERVER_URL=http://your-ansari-backend:8000
   ```

## Running the Service

### Development Mode

```
python -m src.ansari_whatsapp.app.main
```

This will start the service on port 8001 (or the port specified in your .env file).

### Using Docker

To build and run the Docker container:

```
docker build -t ansari-whatsapp .
docker run -p 8001:8001 --env-file .env ansari-whatsapp
```

## API Endpoints

The service exposes the following endpoints:

- `GET /`: Health check endpoint
- `GET /whatsapp/v2`: Webhook verification endpoint for WhatsApp
- `POST /whatsapp/v2`: Main webhook endpoint for receiving WhatsApp messages

## Documentation

### For Newcomers
1. **Quick Start**: Read this README for setup and basic usage
2. **Development Guide**: See `CLAUDE.md` for detailed development instructions, testing, and commands
3. **Architecture Overview**: See `docs/hld/architecture.md` for system design and data flow
4. **Implementation Details**: See `docs/lld/implementation_guide.md` for technical deep-dive

### For Deployment
- **AWS Setup**: See `docs/lld/aws/README.md` for complete deployment walkthrough
- **GitHub Actions**: See `docs/lld/github_actions/README.md` for CI/CD configuration

### For Migration Context
- **Migration Plan**: See `docs/whatsapp_migration_plan/migration_plan.md` for the 4-phase migration from monolith to microservice

## Architecture

The service follows a clean architecture pattern:

- `app/`: FastAPI application with webhook endpoints
- `services/`: Business logic layer (conversation manager, API clients with mock support)
- `presenters/`: Message formatting layer (WhatsApp-specific formatting, RTL support)
- `utils/`: Utility modules (config, logging, parsing, splitting)

## Integration with Ansari Backend

The WhatsApp service communicates with the Ansari backend via 6 API endpoints:

1. **POST /whatsapp/v2/users/register** - User registration
2. **GET /whatsapp/v2/users/exists** - Check user existence
3. **POST /whatsapp/v2/threads** - Create new threads
4. **GET /whatsapp/v2/threads/last** - Get last thread info
5. **GET /whatsapp/v2/threads/{thread_id}/history** - Get thread history
6. **POST /whatsapp/v2/messages/process** - Process messages (streaming)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| HOST | Host to bind the server to | 0.0.0.0 |
| PORT | Port to run the server on | 8001 |
| BACKEND_SERVER_URL | URL of the Ansari backend API | http://localhost:8000 |
| META_API_VERSION | WhatsApp API version | v21.0 |
| META_BUSINESS_PHONE_NUMBER_ID | Your WhatsApp business phone number ID | (required) |
| META_ACCESS_TOKEN_FROM_SYS_USER | Access token for WhatsApp API | (required) |
| META_WEBHOOK_VERIFY_TOKEN | Verify token for WhatsApp webhook | (required) |
| WHATSAPP_CHAT_RETENTION_HOURS | Hours to retain chat history | 3 |
| DEV_MODE | Enable development mode | false |

## Testing

### Running Tests

The project includes comprehensive integration tests for WhatsApp webhook endpoints.

```bash
# Run all tests with detailed output
pytest tests/ -v

# Run with logs displayed
pytest tests/ -v -s

# Run only integration tests
pytest tests/ -m integration -v
```

### Test Configuration

Tests require environment variables to be configured. Key settings:

- **MOCK_ANSARI_CLIENT**: Set to `True` (default) to use mock client (no backend needed), `False` to use real backend
- **MOCK_META_API**: Set to `True` (default) to use mock Meta API, `False` for real API
- **BACKEND_SERVER_URL**: URL of the backend service (required if `MOCK_ANSARI_CLIENT=False`)

**Important**: If `MOCK_ANSARI_CLIENT=False` and the backend is not available, tests will fail with clear instructions on how to fix the issue.

See `tests/README.md` for detailed testing documentation and troubleshooting.

## Troubleshooting

Logs are stored in the `logs/` directory. Check these logs for any errors or issues.

Common issues:
- WhatsApp API connection problems
- Missing or invalid environment variables
- Connection issues with the Ansari backend
- Test failures due to backend availability (see `tests/README.md` for solutions)