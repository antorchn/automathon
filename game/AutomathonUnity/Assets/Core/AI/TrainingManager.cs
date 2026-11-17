#if !UNITY_WEBGL || UNITY_EDITOR
// ZeroMQ server of the training Headless: not compiled in the browser build (no TCP sockets there)
using Automathon.Engine;
using Automathon.Game;
using NetMQ;
using NetMQ.Sockets;
using Newtonsoft.Json;
using System;
using System.Text;

namespace Automathon.AI
{
    public class TrainingManager
    {
        private AIInputProvider selfInputProvider;
        private AIInputProvider enemyInputProvider;

        private ResponseSocket responseSocket;
        private string tcpAddress;

        /// <summary>Upper bound of AIMessage.Frames (10 seconds of game).</summary>
        private const int MaxFramesPerStep = 600;

        // Reported in the GameState sent back (see GameState.DamageDealt, DamageTaken)
        private Fixed damageDealt;
        private Fixed damageTaken;

        public TrainingManager(string tcpAddress)
        {
            GameplayManager.Initialize();

            this.tcpAddress = tcpAddress;
            responseSocket = new ResponseSocket();
            try
            {
                responseSocket.Bind(tcpAddress);
            }
            catch
            {
                throw new Exception("Invalid tcp address.");
            }
            responseSocket.Options.Linger = TimeSpan.Zero;

            selfInputProvider = new AIInputProvider();
            enemyInputProvider = new AIInputProvider();

            GameplayManager.Reset(selfInputProvider, enemyInputProvider);

            Console.WriteLine($"TrainingManager bound to {tcpAddress} and waiting for AI messages...");
        }

        public bool Step()
        {
            byte[] request = responseSocket.ReceiveFrameBytes();

            // Binary step (see BinaryProtocol): answered with a binary state
            if (BinaryProtocol.TryReadStep(request, out AIAction selfAction, out AIAction enemyAction, out int frames))
            {
                Simulate(selfAction, enemyAction, frames);
                responseSocket.SendFrame(BinaryProtocol.WriteState(CurrentState()));
                return false;
            }

            string response = Encoding.UTF8.GetString(request);
            AIMessage msg;
            try
            {
                msg = JsonConvert.DeserializeObject<AIMessage>(response);
            }
            catch (JsonException e)
            {
                // REQ/REP sockets must always answer, otherwise the python side would be stuck forever.
                // Answer with the unchanged state so the trainer can keep going.
                Debug.LogError("Could not parse the message sent from python: " + e.Message);
                responseSocket.SendFrame(JsonConvert.SerializeObject(GameplayManager.GetState(selfInputProvider)));
                return false;
            }

            if (msg.Reset)
                Reset(msg.ResetOptions);
            else if (!msg.DoneWithTraining)
                Simulate(msg.SelfAction, msg.EnemyAction, msg.Frames);

            GameState state = CurrentState();
            if (msg.BinaryState)
                responseSocket.SendFrame(BinaryProtocol.WriteState(state));
            else
                responseSocket.SendFrame(JsonConvert.SerializeObject(state));

            return msg.DoneWithTraining;
        }

        private GameState CurrentState()
        {
            GameState state = GameplayManager.GetState(selfInputProvider);
            state.Frame = GameplayManager.Frame;
            state.DamageDealt = damageDealt;
            state.DamageTaken = damageTaken;
            return state;
        }

        private void Reset(ResetOptions options)
        {
            options ??= new ResetOptions();
            GameRules.Set(options.Rules); // before the reset: ships read their max health from the rules

            // The ship on the left is controlled by the first input provider
            if (options.SelfOnRight)
                GameplayManager.Reset(enemyInputProvider, selfInputProvider, options.Seed, options.MapDensity);
            else
                GameplayManager.Reset(selfInputProvider, enemyInputProvider, options.Seed, options.MapDensity);

            damageDealt = Fixed.Zero;
            damageTaken = Fixed.Zero;
        }

        /// <summary>Simulates `frames` frames with the same actions (at least one), and stops early when the match
        /// ends (a ship dies, or the time limit of the rules is reached).</summary>
        private void Simulate(AIAction selfAction, AIAction enemyAction, int frames)
        {
            selfInputProvider.UpdateFromAction(selfAction);
            enemyInputProvider.UpdateFromAction(enemyAction);

            Tank selfTank = TankOf(selfInputProvider);
            Tank enemyTank = TankOf(enemyInputProvider);
            Fixed selfHealth = selfTank.Health.CurrentHealth;
            Fixed enemyHealth = enemyTank.Health.CurrentHealth;

            frames = Math.Max(1, Math.Min(frames, MaxFramesPerStep));
            for (int i = 0; i < frames; i++)
            {
                GameplayManager.Update();
                if (selfTank.Health.IsDead || enemyTank.Health.IsDead
                    || GameplayManager.State != GameplayManager.GameplayState.Game)
                    break;
            }

            damageTaken = selfHealth - selfTank.Health.CurrentHealth;
            damageDealt = enemyHealth - enemyTank.Health.CurrentHealth;
        }

        private static Tank TankOf(AIInputProvider inputProvider)
            => GameplayManager.Tank1.InputProvider == inputProvider ? GameplayManager.Tank1 : GameplayManager.Tank2;

        public void Dispose()
        {
            responseSocket.Options.Linger = TimeSpan.Zero;
            responseSocket.Unbind(tcpAddress);
            responseSocket.Dispose();
            responseSocket = null;

            GameplayManager.Dispose();
        }
    }

}
#endif
