using Automathon.Game;
using System;

namespace Automathon.AI
{
    /// <summary>
    /// Binary training protocol: the same information as the JSON messages, as arrays of little-endian int32
    /// (Fixed numbers as their raw Q16.16 value), much faster to write and to read than JSON.
    ///
    /// Step request, 12 ints: STEP_TAG, frames, then the self action and the enemy action (5 ints each):
    ///   moving X, moving Y, aiming X, aiming Y (raw Fixed), buttons (1 machine gun, 2 missile, 4 shield, 8 dash).
    /// State: see <see cref="WriteState"/>. A JSON reset with "BinaryState": true is answered with a binary state.
    /// The Python side is PythonAI/bridge/binary.py: both files change together.
    /// </summary>
    public static class BinaryProtocol
    {
        /// <summary>First int of a binary step request (bytes "TEP1"; a JSON message starts with '{').</summary>
        public const int STEP_TAG = 0x31504554;
        public const int STATE_VERSION = 2;

        public const int HEADER_SIZE = 12;  // version, done, outcome, frame, damage dealt, damage taken, 4 counts,
                                            // half size x, y of the safe zone
        public const int SHIP_SIZE = 16;    // exists, team, x, y, vx, vy, aim x, aim y, rotation, health, max health,
                                            // 4 cooldowns (machine gun, missile, shield, dash), unused
        public const int BULLET_SIZE = 6;   // x, y, vx, vy, radius, owner
        public const int MISSILE_SIZE = 8;  // x, y, vx, vy, rotation, radius, owner, armed
        public const int SHIELD_SIZE = 9;   // x, y, vx, vy, rotation, size x, size y, health, owner
        public const int WALL_SIZE = 5;     // x, y, size x, size y, rotation

        private const int STEP_REQUEST_INTS = 12;

        /// <summary>Reads a binary step request; false if the frame is not one (then it is a JSON message).</summary>
        public static bool TryReadStep(byte[] data, out AIAction selfAction, out AIAction enemyAction, out int frames)
        {
            selfAction = enemyAction = null;
            frames = 0;
            if (data.Length != STEP_REQUEST_INTS * 4 || BitConverter.ToInt32(data, 0) != STEP_TAG)
                return false;
            frames = BitConverter.ToInt32(data, 4);
            selfAction = ReadAction(data, 8);
            enemyAction = ReadAction(data, 28);
            return true;
        }

        private static AIAction ReadAction(byte[] data, int offset)
        {
            int buttons = BitConverter.ToInt32(data, offset + 16);
            return new AIAction
            {
                MovingDirection = new FixedVector2(RawFixed(data, offset), RawFixed(data, offset + 4)),
                AimingDirection = new FixedVector2(RawFixed(data, offset + 8), RawFixed(data, offset + 12)),
                MachineGun = (buttons & 1) != 0,
                Missile = (buttons & 2) != 0,
                Shield = (buttons & 4) != 0,
                Dash = (buttons & 8) != 0,
            };
        }

        private static Fixed RawFixed(byte[] data, int offset) => Fixed.FromRaw(BitConverter.ToInt32(data, offset));

        /// <summary>
        /// Binary state: header (HEADER_SIZE), your ship and the enemy ship (SHIP_SIZE each, all zeros if destroyed),
        /// then the bullets, missiles, shields and walls (counts in the header). Teams and owners: 0 Green, 1 Red,
        /// -1 unknown. Outcome: 0 ongoing, 1 win, 2 loss, 3 draw.
        /// </summary>
        public static byte[] WriteState(GameState state)
        {
            int size = HEADER_SIZE + 2 * SHIP_SIZE + BULLET_SIZE * state.BulletStates.Count
                       + MISSILE_SIZE * state.MissileStates.Count + SHIELD_SIZE * state.ShieldStates.Count
                       + WALL_SIZE * state.WallStates.Count;
            int[] ints = new int[size];
            int i = 0;

            ints[i++] = STATE_VERSION;
            ints[i++] = state.Done ? 1 : 0;
            ints[i++] = Array.IndexOf(new[] { "Ongoing", "Win", "Loss", "Draw" }, state.Outcome);
            ints[i++] = state.Frame;
            ints[i++] = state.DamageDealt.Raw;
            ints[i++] = state.DamageTaken.Raw;
            ints[i++] = state.BulletStates.Count;
            ints[i++] = state.MissileStates.Count;
            ints[i++] = state.ShieldStates.Count;
            ints[i++] = state.WallStates.Count;
            i = WriteVector(ints, i, state.ZoneHalfSize);

            i = WriteShip(ints, i, state.SelfShip);
            i = WriteShip(ints, i, state.EnemyShip);

            foreach (Bullet.BulletState b in state.BulletStates)
            {
                i = WriteVector(ints, i, b.Position);
                i = WriteVector(ints, i, b.Velocity);
                ints[i++] = b.Radius.Raw;
                ints[i++] = TeamCode(b.Owner);
            }
            foreach (Missile.MissileState m in state.MissileStates)
            {
                i = WriteVector(ints, i, m.Position);
                i = WriteVector(ints, i, m.Velocity);
                ints[i++] = m.Rotation.Raw;
                ints[i++] = m.Radius.Raw;
                ints[i++] = TeamCode(m.Owner);
                ints[i++] = m.Armed ? 1 : 0;
            }
            foreach (Shield.ShieldState s in state.ShieldStates)
            {
                i = WriteVector(ints, i, s.Position);
                i = WriteVector(ints, i, s.Velocity);
                ints[i++] = s.Rotation.Raw;
                i = WriteVector(ints, i, s.Size);
                ints[i++] = s.Health.Raw;
                ints[i++] = TeamCode(s.Owner);
            }
            foreach (Wall.WallState w in state.WallStates)
            {
                i = WriteVector(ints, i, w.Position);
                i = WriteVector(ints, i, w.Size);
                ints[i++] = w.Rotation.Raw;
            }

            byte[] bytes = new byte[size * 4];
            Buffer.BlockCopy(ints, 0, bytes, 0, bytes.Length);
            return bytes;
        }

        private static int WriteShip(int[] ints, int i, Tank.ShipState ship)
        {
            if (ship == null)
                return i + SHIP_SIZE;
            ints[i++] = 1;
            ints[i++] = TeamCode(ship.Team);
            i = WriteVector(ints, i, ship.Position);
            i = WriteVector(ints, i, ship.Velocity);
            i = WriteVector(ints, i, ship.AimingDirection);
            ints[i++] = ship.Rotation.Raw;
            ints[i++] = ship.Health.Raw;
            ints[i++] = ship.MaxHealth.Raw;
            ints[i++] = ship.MachineGunCooldownFramesLeft;
            ints[i++] = ship.MissileCooldownFramesLeft;
            ints[i++] = ship.ShieldCooldownFramesLeft;
            ints[i++] = ship.DashCooldownFramesLeft;
            return i + 1; // unused slot
        }

        private static int WriteVector(int[] ints, int i, FixedVector2 vector)
        {
            ints[i] = vector.X.Raw;
            ints[i + 1] = vector.Y.Raw;
            return i + 2;
        }

        private static int TeamCode(string team) => team == "Green" ? 0 : team == "Red" ? 1 : -1;
    }
}
