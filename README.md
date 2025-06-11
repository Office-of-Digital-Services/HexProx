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

# Deployment

## Environment and Scaling
The application is designed to be deployed as an Azure Function. It could easily be adapted to an AWS Lambda or other equivalent serverless platform. The only parts specific to Azure are function_app.py, host.json, and local_settings.json. If you deploy elsewhere, we recommend you use a service that scales the number of instances based on load, as Azure does, because some types of requests made to the application cannot be handled fully asynchronously, so the application can block other requests until an existing request is resolved. Instance scaling resolves this, and the application makes no assumptions that all requests for a given user will be to the same instance.

## Steps

Long run, we will recommend that you set your application to redeploy when commits are merged to the `release` branch of this repository. In the near-term or for development:

1. Clone the repository to your computer
2. Open it in VSCode
3. Ensure you have the VSCode Azure Functions extension installed
4. Push the F1 key on your keyboard
5. Type Deploy to Function App and run the command
6. Choose New Function App and follow the prompts.

# Usage
After deployment, you'll have a function at
https://yourfunctionname.azurewebsites.net

From there, we need to obtain an OAuth client ID and secret from the Hexagon administrative user interface. We strongly recommend
you create a special service user account for this purpose. Once you do, the OAuth crentials will be on the new users account page.

From there, you need to base64 encode both the OAuth ID and secret, because they often have symbols that need to be URL encoded,
and the framework used in this application decodes them too early (so they come through garbled). You can base64 locally in your
own browser by opening the developer tools (F12 key), then going to the console tab and typing `btoa("YOUR_CREDENTIAL_HERE")`,
replacing `YOUR_CREDENTIAL HERE` with either you OAuth ID or secret, in succession. When you hit Enter, the base64 value will be
printed below - make sure to remove the surrounding quotes.

If my base64-encoded values were an OAuth ID `24t89oi` and secret `09iqowfeas` (yours will be longer, these are examples), then my new WMTS URL
for use in other applications would be https://yourfunctionname.azurewebsites.net/wmts/24t89oi/09iqowfeas/ - this application encodes
them in the URL path because ArcGIS will reliably pass the whole path through, but not all versions can pass credentials via headers,
authentication processes, or even GET parameters. The proxy will decode the credentials, use them to obtain a token, and generate appropriate
URLs with the currently valid token.

## Security note
Base64 encoding is not security. While it helps that the credentials aren't displayed or transmitted as plain text, anyone with this URL
should be presumed to have credentials that can access data. The proxy will not process requests for them in all cases since it has origin filters, but the OAuth credentials could still be discovered by an informed attacker. Treat the proxy URL as a secret value.