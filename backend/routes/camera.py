"""
routes/camera.py — WebSocket and camera session endpoints.
The WebSocket handler lives in server.py to avoid circular imports
with the AI pipeline. This stub documents the boundary.
"""
# The camera WebSocket endpoint is defined in server.py:
#   @app.websocket("/ws/authority/{code}")
#   async def authority_ws(websocket, code)
#
# and the HTTP session endpoints:
#   GET  /session/{code}
#   POST /session
#   DELETE /session/{code}
#
# These will be extracted to this module in the next refactor iteration
# when the AI pipeline state management is decoupled from the HTTP layer.