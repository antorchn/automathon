from bridge.datatypes import GameState, AIAction, FixedVector2

# Distance (unités Unity) en dessous de laquelle le bot recule au lieu d'avancer.
MIN_DISTANCE = 2


class DummyBot:
    """Bot inactif mais qui tire tout droit."""
    def get_action(self, state: GameState) -> AIAction:
        return AIAction(MovingDirection=(0, 0), AimingDirection=(1, 0), MachineGun=True)


class ParticipantAgent:
    """Fonce sur l'ennemi agressivement (similaire à example_ai.py)."""
    def get_action(self, state: GameState) -> AIAction:
        if not state.SelfShip or not state.EnemyShip:
            return DummyBot().get_action(state)

        to_enemy = state.EnemyShip.Position - state.SelfShip.Position
        distance_sq = to_enemy.length_squared()
        aim = to_enemy.normalized() if distance_sq > 0 else FixedVector2(1, 0)
        forward = distance_sq > MIN_DISTANCE * MIN_DISTANCE

        return AIAction(
            MovingDirection=aim if forward else -aim,
            AimingDirection=aim,
            MachineGun=state.SelfShip.MachineGunCooldownFramesLeft == 0,
            Missile=state.SelfShip.MissileCooldownFramesLeft == 0,
            Shield=state.SelfShip.ShieldCooldownFramesLeft == 0,
            Dash=state.SelfShip.DashCooldownFramesLeft == 0,
        )
