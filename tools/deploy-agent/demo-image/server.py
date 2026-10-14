import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            status = 200
            body = {"status": "ok"}
        elif self.path == "/":
            status = 200
            body = {"app": "JengaSec private-image test"}
        else:
            status = 404
            body = {"error": "not found"}

        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
