#if !UNITY_WEBGL || UNITY_EDITOR
using NetMQ;
using NetMQ.Sockets;
using System;

namespace Automathon.AI
{
    /// <summary>
    /// Agent reached through a ZeroMQ REQ socket (tcp://host:port), as served by PythonAI/bridge/play.py.
    /// Not available in the browser (WebGL), which has no TCP sockets: see the WebSocket link of the game instead.
    /// </summary>
    public class NetMQAgentLink : IAgentLink
    {
        private readonly string address;
        private RequestSocket socket;
        private bool waitingForReply;

        public NetMQAgentLink(string address)
        {
            this.address = address;
            Connect();
        }

        public string Description => $"the Python agent at {address} (agents/run_my_agent.py)";

        public bool HasFailed => socket == null;

        public void Send(string message)
        {
            // A REQ socket accepts a new request only once the previous one got its reply
            if (waitingForReply)
                DropPendingReplies();
            socket.SendFrame(message);
            waitingForReply = true;
        }

        public bool TryReceive(out string reply)
        {
            reply = null;
            if (!waitingForReply || socket == null)
                return false;
            if (!socket.TryReceiveFrameString(TimeSpan.Zero, out reply))
                return false;
            waitingForReply = false;
            return true;
        }

        /// <summary>A REQ socket whose request got no reply can't send anymore: replace it ("Lazy Pirate"), the late
        /// reply is lost with the old socket.</summary>
        public void DropPendingReplies()
        {
            if (!waitingForReply)
                return;
            CloseSocket();
            Connect();
        }

        public void Close() => CloseSocket();

        private void Connect()
        {
            socket = new RequestSocket();
            socket.Options.Linger = TimeSpan.Zero;
            socket.Connect(address);
            waitingForReply = false;
        }

        private void CloseSocket()
        {
            if (socket == null)
                return;
            socket.Options.Linger = TimeSpan.Zero;
            socket.Disconnect(address);
            socket.Dispose();
            socket = null;
            waitingForReply = false;
        }
    }
}
#endif
