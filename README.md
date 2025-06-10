# HexProx - Hexagon WM(T)S Reverse Proxy

So, you want to give a bunch of people in your organization access to an imagery service, huh? But, wait, you don't
have time to field a bunch of request for user onboarding, offboarding, tracking, and password resets,
and it all falls on you as the person with access to the admin interface? You do what any admin does and try to
add it into your existing GIS servers, but something goes wrong. The credential handoff isn't working correctly or
consistently. Users sometimes can't access it in desktop GIS, and sometimes can't access it in web viewers.

Enter HexProx. HexProx moves and encodes your credentials into the path of the URL and then proxies the responses back
to your clients so that ArcGIS Online and Portal for ArcGIS don't have to correctly pass credentials to an authenticated
service (which they don't do correctly). For service information requests, it rewrites the response so that tile
requests come back to the proxy. When proxying tiles, it tries to do so via a redirect to Hexagon's server, but with
a currently valid user authentication token attached to the URL. This works for desktop clients, but doesn't work
with online viewers due to CORS. When it detects it's receiving a request from an online client (currently it checks
for an `Origin` header), it also proxies the full tile data back to the client.

This process works because Hexagon supports token-based authentication, with the token in the URL. The tokens are
short-lived though, up to a day. HexProx hashes client IDs and secrets sent as part of the URL requests and caches the
client it uses to retrieve tiles for that user. It tracks when the token is about to expire and transparently refreshes it
and all following requests will get the new token, whether via redirect or full proxy.

The application is designed to deploy into Azure Functions using an autoscaling deployment. Unfortunately, while
the FastAPI application is asynchronous, we're using requests to download the tiles, and if it has to download tiles,
the download blocks execution for the duration of the download. Azure Functions helps with this, because each request
goes to a different instance of the function, so the blocking behavior doesn't affect your other tiles being loaded.
It also autoscales, so when it's under high load, it can start additional runners to service requests, then spin them
down. Deployment is relatively simple and uses few additional configuration options.