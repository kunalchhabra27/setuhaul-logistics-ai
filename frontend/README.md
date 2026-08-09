# SetuHaul Portal Frontend

> React + TypeScript + Vite portal for SetuHaul's operational workspaces.

This frontend provides the landing page, service-specific auth flow, and portal workspaces for:

- `drivers`
- `tms`
- `wms`
- `checkin`

It talks to:

- Supabase Auth for login, registration, session restore, and logout
- FastAPI for operational API calls

It does **not** implement backend business logic in React.

---

## Environment Variables

Create `frontend/.env` with the same project values used in the root `.env`, but with the `VITE_` prefix required by Vite:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=https://dhwvaqfwdjddmuzzbguc.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_cN-8VtoDPUrfCnpWMXRsPA_v-utcoAU
```

Notes:

- `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` must match the root `.env` values.
- `VITE_API_BASE_URL` should point to the FastAPI origin only, without `/api/v1`.
- Restart the Vite dev server after changing `.env`.

---

## Run

From the repository root:

```bash
cd frontend
npm install
npm run dev
```

Open:

- `http://localhost:5173`

If you are running the backend too:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run uvicorn setuhaul.main:app --reload
```

---

## Auth Flow

Authentication is shared across all portals, but authorization is department-specific. Anyone can use the app by signing in with an account authorized for the selected portal.

Valid portal roles:

- `drivers`
- `tms`
- `wms`
- `checkin`

The frontend stores the role on the Supabase user metadata and restores it from the active session on refresh.

### Route entry points

- `/auth/drivers`
- `/auth/tms`
- `/auth/wms`
- `/auth/checkin`

### Authorization behavior

- A valid Supabase session means the user is authenticated.
- The user's `service_role` determines which portal they may access.
- A user cannot open another portal just because they are logged in.
- Unauthorized access redirects to the matching auth page with a clear access-denied message.

---

## API Integration

The frontend sends authenticated FastAPI requests with:

```http
Authorization: Bearer <session.access_token>
```

Operational endpoints are called through the FastAPI backend only.

Examples:

- TMS shipment list: `/api/v1/tms/shipments`
- Dock suggestions: `/api/v1/dock-scheduler/suggest`
- Check-in status and mutations: `/api/v1/checkins/*`
- Driver chat health: `/api/v1/driver-chat-eta/health`

---

## UX Notes

- The landing page truck animation always moves forward visually.
- The transition truck faces the direction of travel.
- Portal routes are protected centrally, not only by UI hiding.

---

## Build

```bash
cd frontend
npm run build
```
