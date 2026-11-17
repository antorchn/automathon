using Automathon.Engine.Physics;
using Automathon.Game;
using Automathon.Game.Input;
using Automathon.Game.MapSystem;
using Automathon.Utility;
#if !UNITY_WEBGL || UNITY_EDITOR
using NetMQ;
#endif
using System;

namespace Automathon.Engine
{
    public static class GameplayManager
    {
        private static DeferredList<Entity> entities = new();
        public static GameplayState State { get; private set; }
        public static event Action<Entity> EntitySpawned;
        /// <summary>A tank won (winner, and where the loser died). Raised once the frame is fully simulated.</summary>
        public static event Action<Tank.TeamType, FixedVector2> GameEnded;
        /// <summary>Draw: both tanks destroyed in the same frame, or equal health at the time limit (position: between
        /// the two tanks). Raised once the frame is fully simulated.</summary>
        public static event Action<FixedVector2> GameDrawn;

        public static Tank Tank1;
        public static Tank Tank2;

        /// <summary>Number of matches started (Reset) since the start of the program.</summary>
        public static int MatchIndex { get; private set; }

        /// <summary>Frames simulated since the last reset.</summary>
        public static int Frame { get; private set; }
        /// <summary>The match ended because the time limit (GameRules.MatchDuration) was reached.</summary>
        public static bool EndedByTime { get; private set; }
        /// <summary>Winner of a match ended by the time limit (null = draw).</summary>
        public static Tank.TeamType? TimeLimitWinner { get; private set; }

        // End of the match (a tank killed), announced to the views by AnnounceEnd once the frame is fully simulated:
        // both tanks can be killed in the same frame, one after the other, and that is a draw.
        private static bool endAnnouncementPending;
        private static Tank.TeamType pendingWinner;
        private static FixedVector2 pendingLoserPosition;
        /// <summary>Half size of the safe zone, centred on the arena (GameRules.ZoneDamagePerSecond): the whole arena
        /// when there is no zone or before it starts shrinking.</summary>
        public static FixedVector2 ZoneHalfSize { get; private set; } = ArenaHalfSize;

        private static FixedVector2 ArenaHalfSize
            => new FixedVector2(GameplayConstants.ARENA_HALF_WIDTH, GameplayConstants.ARENA_HALF_HEIGHT);

        public enum GameplayState { Lobby, Game }

        public static void Initialize()
        {
            State = GameplayState.Lobby;

            LayerMatrix.Initialize();
            PhysicsManager.Initialize();
        }

        private static void GenerateRandomMap(int? seed, double? density)
        {
            if (seed.HasValue || density.HasValue)
                RandomMapGenerator.GenerateRandomMap(seed ?? Environment.TickCount, density ?? RandomMapGenerator.DefaultDensity);
            else
                RandomMapGenerator.GenerateRandomMap();
        }

        /// <summary>
        /// False while the next frame of the match must wait for an input (an AI agent that hasn't answered yet). Never
        /// blocks: the visual game calls it on each Unity frame and only calls Update once it is true, which keeps the
        /// browser (WebGL) responsive. Update called without it waits for the inputs itself (InputProvider.BeforeFrame).
        /// </summary>
        public static bool InputsReady()
        {
            if (State != GameplayState.Game)
                return true;
            // Both are asked on every call, so that both agents get their request on the same Unity frame
            bool ready1 = Tank1?.InputProvider?.IsReadyForFrame() ?? true;
            bool ready2 = Tank2?.InputProvider?.IsReadyForFrame() ?? true;
            return ready1 && ready2;
        }

        public static void Update()
        {
            // Inputs are read before any entity moves, so both tanks decide on the same state (as in the training
            // Headless). AI agents decide only once every GameplayConstants.AI_DECISION_INTERVAL frames.
            if (State == GameplayState.Game)
            {
                Tank1?.InputProvider?.BeforeFrame();
                Tank2?.InputProvider?.BeforeFrame();
            }

            EntityUpdateLoop();

            ProcessAllEntityChanges();

            PhysicsManager.Step();

            Frame++;
            UpdateZone();
            Fixed duration = GameRules.Current.MatchDuration;
            if (State == GameplayState.Game && duration > 0 && Frame >= GameplayConstants.SecondsToFrames(duration))
                EndGameByTime();

            AnnounceEnd();
        }

        /// <summary>Raises GameEnded or GameDrawn for a match ended by a kill during the frame just simulated.</summary>
        private static void AnnounceEnd()
        {
            if (!endAnnouncementPending)
                return;
            endAnnouncementPending = false;

            if (Tank1.Health.IsDead && Tank2.Health.IsDead)
                GameDrawn?.Invoke(Between(Tank1, Tank2));
            else
                GameEnded?.Invoke(pendingWinner, pendingLoserPosition);
        }

        private static FixedVector2 Between(Tank a, Tank b) => (a.Position + b.Position) * Fixed.Ratio(1, 2);

        /// <summary>
        /// Safe zone (GameRules.ZoneDamagePerSecond > 0): sets its size for the frame just simulated, then damages the
        /// tanks whose centre is outside it (a dashing tank is invincible, as against any other damage).
        /// </summary>
        private static void UpdateZone()
        {
            GameRules rules = GameRules.Current;
            if (rules.ZoneDamagePerSecond <= 0)
            {
                ZoneHalfSize = ArenaHalfSize;
                return;
            }

            int start = GameplayConstants.SecondsToFrames(rules.ZoneStart);
            int shrink = Math.Max(1, GameplayConstants.SecondsToFrames(rules.ZoneShrinkDuration));
            int elapsed = Math.Clamp(Frame - start, 0, shrink);
            Fixed scale = Fixed.One - Fixed.Ratio(elapsed, shrink) * (Fixed.One - rules.ZoneFinalScale);
            ZoneHalfSize = ArenaHalfSize * scale;

            if (State != GameplayState.Game)
                return;
            Fixed damage = rules.ZoneDamagePerSecond * GameplayConstants.DELTA_TIME;
            foreach (Tank tank in new[] { Tank1, Tank2 })
            {
                if (tank != null && !tank.Health.IsDead
                    && (tank.Position.X.Abs() > ZoneHalfSize.X || tank.Position.Y.Abs() > ZoneHalfSize.Y))
                    tank.Health.Damage(damage);
            }
        }

        /// <summary>Time limit reached: the tank with the most health wins, equal health is a draw.</summary>
        private static void EndGameByTime()
        {
            State = GameplayState.Lobby;
            EndedByTime = true;

            Fixed health1 = Tank1.Health.CurrentHealth;
            Fixed health2 = Tank2.Health.CurrentHealth;
            if (health1 == health2)
            {
                TimeLimitWinner = null;
                GameDrawn?.Invoke(Between(Tank1, Tank2));
            }
            else
            {
                TimeLimitWinner = health1 > health2 ? Tank1.Team : Tank2.Team;
                Tank loser = health1 > health2 ? Tank2 : Tank1;
                GameEnded?.Invoke(TimeLimitWinner.Value, loser.Position);
            }
        }

        /// <summary>
        /// Stops the match without a winner (a replay recorded up to its time cap): the views get it as a draw
        /// (GameDrawn, between the two ships), while the state stays "Ongoing" for the AIs, as on the Headless when a
        /// match is cut at its cap. Not a rule of the game: nothing in the simulation calls it.
        /// </summary>
        public static void StopWithoutWinner()
        {
            if (State != GameplayState.Game)
                return;
            State = GameplayState.Lobby;
            GameDrawn?.Invoke(Between(Tank1, Tank2));
        }

        /// <summary>
        /// Starts a new match: inputProvider1 controls the tank on the left, inputProvider2 the one on the right.
        /// mapSeed / mapDensity: see <see cref="RandomMapGenerator.GenerateRandomMap(int, double)"/> (null = random map,
        /// default density).
        /// </summary>
        public static void Reset(InputProvider inputProvider1, InputProvider inputProvider2,
                                 int? mapSeed = null, double? mapDensity = null)
        {
            ClearArena();

            GenerateRandomMap(mapSeed, mapDensity);

            Tank1 = Instantiate(new Tank(Tank.TeamType.Green, new FixedVector2(-10, 0), inputProvider1));
            Tank2 = Instantiate(new Tank(Tank.TeamType.Red, new FixedVector2(10, 0), inputProvider2));

            ProcessAllEntityChanges();

            Frame = 0;
            ZoneHalfSize = ArenaHalfSize;
            MatchIndex++;
            EndedByTime = false;
            TimeLimitWinner = null;
            endAnnouncementPending = false;
            State = GameplayState.Game;
        }

        /// <summary>Removes every entity (tanks, walls, bullets...) right away: the arena stays empty until the next Reset.</summary>
        public static void ClearArena()
        {
            foreach (Entity entity in entities.Items) Destroy(entity);

            ProcessAllEntityChanges();
        }

        /// <summary>
        /// A tank was killed (called once per tank). The match ends at once (State), and the views learn the result at the
        /// end of the frame (AnnounceEnd): if the other tank is killed in the same frame, it is a draw, as GetState reports
        /// it. The simulation keeps running after the end (bullets still fly): a tank killed in a later frame changes
        /// nothing.
        /// </summary>
        public static void EndGame(Tank.TeamType loser, FixedVector2 loserPosition)
        {
            if (State != GameplayState.Game && !endAnnouncementPending)
                return;

            State = GameplayState.Lobby;
            if (endAnnouncementPending)
                return;  // second kill of the frame: AnnounceEnd sees both tanks dead

            endAnnouncementPending = true;
            pendingWinner = loser == Tank1.Team ? Tank2.Team : Tank1.Team;
            pendingLoserPosition = loserPosition;
        }

        public static GameState GetState(InputProvider self)
        {
            GameState gameState = new GameState();
            int tankCount = 0;

            foreach (Entity entity in entities.Items)
            {
                if (entity is Tank t)
                {
                    // A tank killed during this frame is only removed at the start of the next one: it already counts
                    // as gone, so the end of the match is reported on the frame of the kill
                    if (t.Health.IsDead)
                        continue;
                    tankCount++;
                    if (t.InputProvider == self)
                        gameState.SelfShip = (Tank.ShipState)t.GetState();
                    else
                        gameState.EnemyShip = (Tank.ShipState)t.GetState();
                }

                else if (entity is Bullet)
                    gameState.BulletStates.Add((Bullet.BulletState)entity.GetState());
                else if (entity is Missile)
                    gameState.MissileStates.Add((Missile.MissileState)entity.GetState());
                else if (entity is Wall)
                    gameState.WallStates.Add((Wall.WallState)entity.GetState());
                else if (entity is Shield)
                    gameState.ShieldStates.Add((Shield.ShieldState)entity.GetState());

            }

            gameState.Done = tankCount <= 1 || EndedByTime;
            gameState.ZoneHalfSize = ZoneHalfSize;

            bool selfAlive = gameState.SelfShip != null, enemyAlive = gameState.EnemyShip != null;
            if (EndedByTime && selfAlive && enemyAlive)
            {
                Tank selfTank = Tank1.InputProvider == self ? Tank1 : Tank2;
                gameState.Outcome = TimeLimitWinner == null ? "Draw" : TimeLimitWinner == selfTank.Team ? "Win" : "Loss";
            }
            else
                gameState.Outcome = selfAlive && enemyAlive ? "Ongoing" : selfAlive ? "Win" : enemyAlive ? "Loss" : "Draw";

            return gameState;
        }

        public static void EntityUpdateLoop()
        {
            foreach (Entity entity in entities.Items)
                entity.Update();
        }

        private static void ProcessAllEntityChanges()
        {
            entities.ProcessChanges((e) =>
            {
                EntitySpawned?.Invoke(e);
                e.Start();
            }, (e) => e.OnDestroyed()); //Instanciate/Destroy every entity that needs to be

            foreach (Entity entity in entities.Items) //Add/Remove new entity components
                entity.ApplyComponentsChanges();
        }

        public static T Instantiate<T>(T entity) where T : Entity
        {
            entities.Add(entity);
            return entity;
        }

        public static void Destroy(Entity entity)
        {
            entities.Remove(entity);
        }

        public static void ChangeState(GameplayState newState)
        {
            State = newState;
            //maybe emit an event here in the future
        }

        public static void Dispose()
        {
            ClearArena();

            PhysicsManager.Dispose();

#if !UNITY_WEBGL || UNITY_EDITOR
            NetMQConfig.Cleanup(false);
#endif
        }
    }
}