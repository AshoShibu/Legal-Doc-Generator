# React Frontend

This folder contains the React workspace for the Maharashtra Legal Document Drafter. It talks to the FastAPI backend in `../backend/main.py`.

## Development

Start the backend from the repository root:

```bash
uvicorn backend.main:app --reload
```

Then start the React app from this folder:

```bash
npm install
npm start
```

The UI runs at `http://localhost:3000` and expects the API at `http://localhost:8000` by default.

## Environment

Set `REACT_APP_API_BASE_URL` if your backend is running somewhere else:

```bash
REACT_APP_API_BASE_URL=http://localhost:8000
```

## Scripts

- `npm start` starts the development server
- `npm run build` creates a production build in `build/`
- `npm test` runs the test suite
