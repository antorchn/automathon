using Automathon.Engine;
using Automathon.Game;
using Automathon.Game.Input;
using Newtonsoft.Json;
using System;
using Stopwatch = System.Diagnostics.Stopwatch;

namespace Automathon.AI
{
    public class AIInputProvider : InputProvider
    {
        /// <summary>Time given by default to the agent to answer a decision request.</summary>
        public static readonly TimeSpan DefaultResponseTimeout = TimeSpan.FromMilliseconds(500);

        private AIAction lastAction;
        private IAgentLink link;
        private bool failureReported;

        /// <summary>Time given to the agent to answer; after it, the ship keeps its last action until the next decision.</summary>
        public TimeSpan ResponseTimeout = DefaultResponseTimeout;

        // Decision of the current frame: requested to the agent, then decided (reply, timeout or lost connection)
        private int requestedMatch = -1, requestedFrame = -1;
        private int decidedMatch = -1, decidedFrame = -1;
        private readonly Stopwatch waitTimer = new Stopwatch();

        // Health of both ships at this AI's previous decision, for GameState.DamageDealt / DamageTaken
        private Fixed selfHealthAtLastDecision;
        private Fixed enemyHealthAtLastDecision;

        /// <summary>AI whose actions are given from outside with UpdateFromAction (training Headless, replays).</summary>
        public AIInputProvider() : this((IAgentLink)null) { }

#if !UNITY_WEBGL || UNITY_EDITOR
        /// <summary>
        /// AI served by a ZeroMQ REP socket at tcpAddress (PythonAI/bridge/play.py). Null = actions given from outside
        /// with UpdateFromAction. Not available in the browser (no TCP sockets there).
        /// </summary>
        public AIInputProvider(string tcpAddress) : this(tcpAddress == null ? null : new NetMQAgentLink(tcpAddress)) { }
#endif

        /// <summary>AI reached through link (null = actions given from outside with UpdateFromAction).</summary>
        public AIInputProvider(IAgentLink link)
        {
            lastAction = new AIAction();
            lastAction.MovingDirection = FixedVector2.Right;
            lastAction.AimingDirection = FixedVector2.Right;

            this.link = link;
        }

        /// <summary>Sends "Ping" to the agent: PollPing then tells when it answered "Pong".</summary>
        public void StartPing()
        {
            link.Send("Ping");
            waitTimer.Restart();
        }

        /// <summary>True: the agent answered "Pong". False: wrong answer, lost connection or no answer within timeout
        /// (the ping can then be started again). Null: still waiting.</summary>
        public bool? PollPing(TimeSpan timeout)
        {
            if (link.TryReceive(out string reply))
                return reply == "Pong";
            if (link.HasFailed)
                return false;
            if (waitTimer.Elapsed <= timeout)
                return null;
            link.DropPendingReplies();
            return false;
        }

        /// <summary>
        /// Asks the AI for an action, at the start of the frame (same state for both ships) and only once every
        /// GameplayConstants.AI_DECISION_INTERVAL frames (0, 4, 8... from the start of the match, the same frames for both
        /// ships). In between, the last action is kept: exactly what the training Headless does with a frame skip of
        /// AI_DECISION_INTERVAL. In the Headless itself (no link), actions come from TrainingManager.
        ///
        /// Never blocks: false means the request is sent and the reply hasn't arrived yet, so the frame must wait
        /// (see GameplayManager.InputsReady). After ResponseTimeout, the AI keeps its last action and the frame goes on.
        /// </summary>
        public override bool IsReadyForFrame()
        {
            if (link == null)
                return true;

            int match = GameplayManager.MatchIndex, frame = GameplayManager.Frame;
            if (frame % GameplayConstants.AI_DECISION_INTERVAL != 0 || (decidedMatch == match && decidedFrame == frame))
                return true;

            if (requestedMatch != match || requestedFrame != frame)
            {
                GameState state = GameplayManager.GetState(this);
                FillMatchProgress(state);
                link.Send(JsonConvert.SerializeObject(state));
                requestedMatch = match;
                requestedFrame = frame;
                waitTimer.Restart();
            }

            if (link.TryReceive(out string reply))
                HandleReply(reply);
            else if (link.HasFailed)
            {
                if (!failureReported)
                    Debug.LogError($"Lost the connection to {link.Description}: the ship keeps its last action.");
                failureReported = true;
            }
            else if (waitTimer.Elapsed > ResponseTimeout)
            {
                Debug.LogError($"Timeout: {link.Description} took more than {ResponseTimeout.TotalMilliseconds:0} ms to "
                               + "answer, the ship keeps its last action. Make sure the agent is running.");
                link.DropPendingReplies();
            }
            else
                return false;

            decidedMatch = match;
            decidedFrame = frame;
            return true;
        }

        /// <summary>For callers that don't wait with IsReadyForFrame: blocks until the reply or the timeout. Never in
        /// the browser, which has a single thread (a reply can only arrive between two Unity frames): there, a frame
        /// played before the agent answered keeps the ship's last action.</summary>
        public override void BeforeFrame()
        {
            if (link == null)
                return;
#if !UNITY_WEBGL || UNITY_EDITOR
            while (!IsReadyForFrame())
                System.Threading.Thread.Sleep(1);
#else
            if (!IsReadyForFrame())
            {
                Debug.LogError("Frame played before the agent answered (GameplayManager.InputsReady not called): the ship "
                               + "keeps its last action.");
                link.DropPendingReplies(); // its late reply must not be taken as the next decision
                decidedMatch = requestedMatch;
                decidedFrame = requestedFrame;
            }
#endif
        }

        private void HandleReply(string response)
        {
            AIMessage msg;
            try
            {
                msg = JsonConvert.DeserializeObject<AIMessage>(response);
            }
            catch
            {
                Debug.LogError("Count not parse JSON sent from python");
                return;
            }

            if (msg.Reset)
            {
                Debug.LogError("The AI requested a reset of the game, but it is not in training mode.");
                return;
            }

            if (msg.DoneWithTraining)
            {
                Debug.LogError("The AI requested to be done with training, but it is not in training mode.");
                return;
            }

            if (msg.SelfAction != null)
                UpdateFromAction(msg.SelfAction);
        }

        /// <summary>
        /// Frame of the match, and damage dealt and taken since this AI's previous decision: the same values as the
        /// state the training Headless sends after a step of AI_DECISION_INTERVAL frames (see TrainingManager).
        /// </summary>
        private void FillMatchProgress(GameState state)
        {
            Tank self = GameplayManager.Tank1?.InputProvider == this ? GameplayManager.Tank1 : GameplayManager.Tank2;
            Tank enemy = self == GameplayManager.Tank1 ? GameplayManager.Tank2 : GameplayManager.Tank1;
            if (self == null || enemy == null)
                return;

            Fixed selfHealth = self.Health.CurrentHealth, enemyHealth = enemy.Health.CurrentHealth;
            if (GameplayManager.Frame == 0)
            {
                selfHealthAtLastDecision = selfHealth;
                enemyHealthAtLastDecision = enemyHealth;
            }

            state.Frame = GameplayManager.Frame;
            state.DamageTaken = selfHealthAtLastDecision - selfHealth;
            state.DamageDealt = enemyHealthAtLastDecision - enemyHealth;
            selfHealthAtLastDecision = selfHealth;
            enemyHealthAtLastDecision = enemyHealth;
        }

        public void UpdateFromAction(AIAction action)
        {
            //Attention : Data du user donc programmer defensivement pour eviter les erreurs
            if (action == null)
                return;

            // Fixed values saturate instead of overflowing, and normalization works on the whole range,
            // so any user value ends up as a valid direction.
            if (action.MovingDirection.LengthSquaredLong() > FixedLong.One)
                action.MovingDirection = action.MovingDirection.Normalized();

            action.AimingDirection = action.AimingDirection.Normalized();
            if (action.AimingDirection == FixedVector2.Zero)
                action.AimingDirection = lastAction.AimingDirection;

            lastAction = action;
        }

        public void EndLife()
        {
            base.OnDestroyed();

            link?.Close();
            link = null;
        }

        public override FixedVector2 GetAimingDir()
            => lastAction.AimingDirection;

        public override FixedVector2 GetMovementDir()
            => lastAction.MovingDirection;

        public override bool ShouldDash()
            => lastAction.Dash;

        public override bool ShouldMissile()
            => lastAction.Missile;

        public override bool ShouldShield()
            => lastAction.Shield;

        public override bool ShouldShoot()
            => lastAction.MachineGun;
    }

}
