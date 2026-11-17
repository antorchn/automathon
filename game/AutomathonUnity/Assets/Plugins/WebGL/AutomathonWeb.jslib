// Browser side of WebSocketAgentLink (Game/Features/Web): WebSockets of the page, polled by the game on each frame.
// Every function is non-blocking: the game sends, and takes the replies once they have arrived.
mergeInto(LibraryManager.library, {

  AgentSocket_Open: function (urlPtr) {
    var url = UTF8ToString(urlPtr);
    if (!Module.automathonSockets) {
      Module.automathonSockets = {};
      Module.automathonNextSocket = 1;
    }
    var id = Module.automathonNextSocket++;
    // state: 0 connecting, 1 open, 2 failed or closed
    var entry = { socket: null, state: 0, outgoing: [], incoming: [] };
    Module.automathonSockets[id] = entry;
    try {
      entry.socket = new WebSocket(url);
    } catch (e) {
      console.error("[Automathon] Invalid agent address " + url + ": " + e);
      entry.state = 2;
      return id;
    }
    entry.socket.onopen = function () {
      entry.state = 1;
      for (var i = 0; i < entry.outgoing.length; i++) entry.socket.send(entry.outgoing[i]);
      entry.outgoing = [];
    };
    entry.socket.onmessage = function (event) {
      if (typeof event.data === "string") entry.incoming.push(event.data);
    };
    entry.socket.onerror = function () { entry.state = 2; };
    entry.socket.onclose = function () { entry.state = 2; };
    return id;
  },

  AgentSocket_Send: function (id, messagePtr) {
    var entry = Module.automathonSockets && Module.automathonSockets[id];
    if (!entry || entry.state === 2) return;
    var message = UTF8ToString(messagePtr);
    if (entry.state === 0) entry.outgoing.push(message);
    else entry.socket.send(message);
  },

  // Oldest reply not taken yet, or null. The string is freed by the C# marshaller.
  AgentSocket_Receive: function (id) {
    var entry = Module.automathonSockets && Module.automathonSockets[id];
    if (!entry || entry.incoming.length === 0) return null;
    var message = entry.incoming.shift();
    var size = lengthBytesUTF8(message) + 1;
    var buffer = _malloc(size);
    stringToUTF8(message, buffer, size);
    return buffer;
  },

  AgentSocket_State: function (id) {
    var entry = Module.automathonSockets && Module.automathonSockets[id];
    return entry ? entry.state : 2;
  },

  AgentSocket_Close: function (id) {
    var entry = Module.automathonSockets && Module.automathonSockets[id];
    if (!entry) return;
    try { if (entry.socket) entry.socket.close(); } catch (e) { }
    delete Module.automathonSockets[id];
  },
});
