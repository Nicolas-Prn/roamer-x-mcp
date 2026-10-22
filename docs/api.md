# ICONICS IBSS API notes

Roamer X (mobile app) and IBSS Hub (web portal) are front-ends of the ICONICS **Intelligent Building
Software Stack** (IBSS). Each customer has its own environment ("tenant").

| | ULB/VUB (`uob`) |
|---|---|
| Web hub | `https://hub.ibss.uob.iconics.cloud/` |
| API | `https://api.ibss.uob.iconics.cloud` |
| Swagger UI | `https://api.ibss.uob.iconics.cloud/swagger/index.html` |

Snapshots of the two spec versions live in [`spec/`](spec/) (`ibss-v1.json`: 241 operations,
`ibss-v2.json`: 187 operations). Refresh them with:

```sh
curl -s https://api.ibss.uob.iconics.cloud/swagger/v1/swagger.json -o docs/spec/ibss-v1.json
curl -s https://api.ibss.uob.iconics.cloud/swagger/v2/swagger.json -o docs/spec/ibss-v2.json
```

## Authentication

Every call needs `Authorization: Bearer <JWT>`. The tenant supports several identity providers
(native, Azure AD, Okta); the `uob` tenant uses **Microsoft Entra ID** (observed on 2026-10-22):

- The Hub is an MSAL.js single-page app. After the Microsoft sign-in, it stores the Entra token in
  `localStorage["token"]` and sends it as is as the bearer token to `api.ibss.uob.iconics.cloud`.
- Token: issuer `https://login.microsoftonline.com/30a5145e-75bd-4212-bb02-8ff9c0ea4ae9/v2.0`,
  audience `c0e9d517-c649-4aa3-9af4-38abe198d31d` (the Hub's app registration), **lifetime about 65 min**.
- MSAL renews it silently in the browser as long as the Microsoft session is alive.

`roamer-x-mcp login` opens the Hub in a dedicated browser profile where the user signs in; afterwards the
client reopens it headless to read the renewed token (see `src/roamer_x_mcp/auth.py`).

## Observed Hub calls

- Root node is `1`, the LIC building is node `36` (`/flex-home/?building=36`).
- My bookings (this week):
  `GET /v2/1/bookings/my-bookings?$select=Node_Id,Space_Id,Space_Name,Booking_Id,Booking_Name,Booking_Start,Booking_End,Booking_Status,…&$filter=Booking_Start ge datetime'2026-10-18T22:00:00.000Z' and Booking_End lt datetime'2026-10-25T23:00:00.000Z'&$top=100&$orderby=Booking_Start`

## Findings that shape the client (2026-10-22)

- Space routes (`v2/{node}/spaces/{id}`, `.../booking-policy`) only work on the space's **floor** node
  (404 on the building node). Searches use the building node.
- `spaces/search` rejects filters without `AvailableBetween(datetime'…Z', datetime'…Z')` (HTTP 400). The Hub's
  filter grammar: `(Space_IsEnabled eq 1) and (Space_Type eq 'Desk') and (Node_Id eq 39) and
  AvailableBetween(...) and Space_Capacity ge 2`.
- List responses use `Value` (capital V). Following `@odata.nextLink` as is fails (HTTP 500, the filter is
  dropped): resend the original query with `$skiptoken` added, as the Hub does.
- Booking creation: `POST /v2/{floor}/bookings` with `SpaceId`, `Booking_Start` / `Booking_End`,
  `Booking_Name`, `Booking_AutoCheckIn`, `_CreatedBy` (user email)… Send times in **UTC** (`…Z`): local
  times with an offset (`10:00+02:00`, what the Hub sends) are shifted twice by the API (seen as 06:00) and
  rejected as "outside the office hours". Verified end to end on 2026-10-22 (book, list, cancel).
- The create response may not include `Booking_Id`; `my-bookings` returns times in UTC and shows a new or
  cancelled booking only after a few seconds.
- Cancellation: `PATCH /v2/{node}/bookings/{id}/cancel` (no body).
- Error payloads carry the reason in `Errors[].Detail` (e.g. "cannot be booked outside the office hours").
- `bookings-on-this-day` exposes other users' names and emails: the MCP does not surface it.
- Booking policies (`v2/{building}/bookingpolicies/{id}`, JSON string in `Booking_Policy`) hold the rules:
  `BookingDuration` (min), `BookingHorizon.Maximum` (days), `BookableTime`, `ApprovalPolicies`.
  At the LIC: study seats ("Desk", policies *Study Seat Library/Exam*) 30–240 min, 7 days ahead, 08:00–23:00;
  meeting rooms are mostly *Office Policy* (STAFF ONLY, approval).

## Conventions

- Most routes are scoped by `{nodeid}`: a node of the location hierarchy (organisation → region →
  building → floor → zone). Get the tree with `GET /v1/{nodeid}/Nodes`.
- v2 list endpoints accept OData parameters (`$filter`, `$select`, `$orderby`, `$top`) and page with
  `@odata.nextLink` / `$skiptoken` (25 items by default).
- Field names use `Pascal_Snake` (`Space_Id`, `Booking_Start`…); dates are ISO 8601.

## Endpoints used (user scope)

| Purpose | Endpoint |
|---|---|
| Location tree | `GET /v1/{nodeid}/Nodes`, `GET /v1/{nodeid}/Floors` |
| Space types | `GET /v2/{nodeid}/nodes/space-types` |
| Search spaces | `GET /v2/{nodeid}/spaces/search` |
| Space details / policy | `GET /v2/{nodeid}/spaces/{id}`, `GET /v2/{nodeid}/spaces/{spaceid}/booking-policy` |
| Space schedule | `GET /v2/{nodeid}/spaces/{id}/bookings-on-this-day` |
| My bookings | `GET /v2/{nodeid}/bookings/my-bookings` |
| Book | `POST /v2/{nodeid}/bookings` (`SpaceBookRequestV2`) |
| Change | `PATCH /v2/{nodeid}/bookings/{bookingid}` |
| Cancel | `PATCH /v2/{nodeid}/bookings/{bookingid}/cancel` |
| Check in / out | `PATCH /v2/{nodeid}/spaces/{id}/checkin`, `.../checkout` |
| Favourites | `GET /v1/{nodeid}/UserPreferences`, `POST/DELETE /v1/{nodeid}/UserPreferences/{spaceId}/favourite-space` |
| Platform version | `GET /v1/Service/version` |

Admin-only routes (spaces CRUD, booking policies, approvals, imports/exports) are out of scope.

## LIC booking rules (from lic.brussels)

- Blocks of up to 4 hours.
- Students: meeting rooms up to 1 week ahead. Staff: staff rooms up to 60 days ahead.
- Some rooms are staff-only.
- Learning Theatre and Board Room are not bookable through Roamer (VUB reservation form).
