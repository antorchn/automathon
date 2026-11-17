"""
Data exchanged with the game.

Every physical quantity (positions, sizes, velocities, rotations, health...) is a :class:`Fixed`
(Q16.16 fixed point number, see ``bridge/fixed.py``) and vectors are :class:`FixedVector2`.
They behave like floats/2D vectors, and the values are exactly the ones used by the game simulation.

Units: 1 = 1 world unit (1 Unity unit), angles are in radians, velocities in units per second.
The map is about 27.5 x 15.5 units, centered on (0, 0).
"""

import math
from typing import Annotated, Any, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bridge.fixed import Fixed, _rounded_divide, _saturate, FRACTIONAL_BITS, ONE_RAW

# En match, un agent decide une fois toutes les DECISION_INTERVAL frames (60 / 4 = 15 Hz), aux memes frames que son
# adversaire, et garde son action entre-temps. Meme valeur que GameplayConstants.AI_DECISION_INTERVAL (C#). C'est le
# frame_skip par defaut des environnements : entrainement, evaluation et matchs se jouent dans les memes conditions.
DECISION_INTERVAL = 4

# Bords de l'arene : quatre murs invisibles (RandomMapGenerator.cs) qui arretent les vaisseaux mais que les balles
# traversent. Ils ne sont pas dans GameState.WallStates. Valeurs : position de leur face interieure, en unites, l'arene
# etant centree en (0, 0). Le centre d'un vaisseau (rayon 0,419) s'arrete donc vers |x| = 13,3 et |y| = 7,3 (il peut
# s'enfoncer de quelques centiemes en poussant contre le bord : les contacts du moteur sont souples).
ARENA_HALF_WIDTH = 13.70
ARENA_HALF_HEIGHT = 7.70


class FixedVector2(BaseModel):
    """
    2D vector of Fixed numbers. Can be built with ``FixedVector2(1, 0.5)``, ``FixedVector2(X=1, Y=0.5)`` or from a
    ``(x, y)`` tuple anywhere a FixedVector2 is expected. Assigning floats/ints to X or Y converts them to Fixed.

    Supports ``+``, ``-``, ``* scalar``, ``/ scalar``, ``dot``, ``cross``, ``length``, ``normalized``...
    ``length``, ``length_squared``, ``dot``, ``cross`` and ``normalized`` give exactly the same results as the game.
    """

    model_config = ConfigDict(validate_assignment=True)

    X: Fixed = Fixed.ZERO
    Y: Fixed = Fixed.ZERO

    def __init__(self, *args: Any, **data: Any):
        if args:
            if len(args) == 1 and isinstance(args[0], (tuple, list)):
                args = tuple(args[0])
            if len(args) != 2:
                raise TypeError("FixedVector2 takes 2 positional arguments (x, y)")
            data.setdefault("X", args[0])
            data.setdefault("Y", args[1])
        super().__init__(**data)

    # Ce constructeur ne sert qu'aux arguments positionnels. Le marquer comme constructeur de base
    # evite que pydantic l'appelle a chaque vecteur lu dans le JSON (double validation, ~2x plus lent).
    __init__.__pydantic_base_init__ = True

    @model_validator(mode="before")
    @classmethod
    def _from_sequence(cls, data: Any) -> Any:
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return {"X": data[0], "Y": data[1]}
        return data

    # ------------------------------------------------------------------ constructors

    @classmethod
    def zero(cls) -> "FixedVector2":
        return cls(0, 0)

    @classmethod
    def from_angle(cls, radians: Any) -> "FixedVector2":
        """Unit vector (cos, sin) of an angle in radians (within 1/65536 of the game's CORDIC implementation)."""
        a = float(radians)
        return cls(math.cos(a), math.sin(a))

    # ------------------------------------------------------------------ operators

    def __add__(self, other: "FixedVector2") -> "FixedVector2":
        if not isinstance(other, FixedVector2):
            return NotImplemented
        return FixedVector2(self.X + other.X, self.Y + other.Y)

    def __sub__(self, other: "FixedVector2") -> "FixedVector2":
        if not isinstance(other, FixedVector2):
            return NotImplemented
        return FixedVector2(self.X - other.X, self.Y - other.Y)

    def __neg__(self) -> "FixedVector2":
        return FixedVector2(-self.X, -self.Y)

    def __mul__(self, scalar: Any) -> "FixedVector2":
        if isinstance(scalar, FixedVector2):
            return NotImplemented
        return FixedVector2(self.X * scalar, self.Y * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar: Any) -> "FixedVector2":
        if isinstance(scalar, FixedVector2):
            return NotImplemented
        return FixedVector2(self.X / scalar, self.Y / scalar)

    def __str__(self) -> str:
        return f"({self.X}, {self.Y})"

    def __repr__(self) -> str:
        return f"FixedVector2({self.X}, {self.Y})"

    # ------------------------------------------------------------------ math (bit exact with the game)

    def dot(self, other: "FixedVector2") -> Fixed:
        exact = self.X.raw * other.X.raw + self.Y.raw * other.Y.raw  # Q32.32
        return Fixed.from_raw((exact + (1 << 15)) >> 16)

    def cross(self, other: "FixedVector2") -> Fixed:
        """X * other.Y - Y * other.X"""
        exact = self.X.raw * other.Y.raw - self.Y.raw * other.X.raw
        return Fixed.from_raw((exact + (1 << 15)) >> 16)

    def length_squared(self) -> Fixed:
        return self.dot(self)

    def length(self) -> Fixed:
        n = self.X.raw * self.X.raw + self.Y.raw * self.Y.raw
        root = math.isqrt(n)
        if n - root * root > root:
            root += 1
        return Fixed.from_raw(root)

    def normalized(self) -> "FixedVector2":
        """Unit vector with the same direction (the zero vector stays zero)."""
        x, y = self.X.raw, self.Y.raw
        if x == 0 and y == 0:
            return FixedVector2(0, 0)
        max_abs = max(abs(x), abs(y))
        while max_abs < (1 << 29):
            x <<= 1
            y <<= 1
            max_abs <<= 1
        n = x * x + y * y
        length = math.isqrt(n)
        if n - length * length > length:
            length += 1
        return FixedVector2(
            Fixed.from_raw(_rounded_divide(x << FRACTIONAL_BITS, length)),
            Fixed.from_raw(_rounded_divide(y << FRACTIONAL_BITS, length)),
        )

    def distance_to(self, other: "FixedVector2") -> Fixed:
        return (self - other).length()

    def angle(self) -> Fixed:
        """Angle in radians in ]-pi, pi] (within 1/65536 of the game's CORDIC implementation)."""
        return Fixed(math.atan2(float(self.Y), float(self.X)))

    def rotated(self, radians: Any) -> "FixedVector2":
        c, s = math.cos(float(radians)), math.sin(float(radians))
        x, y = float(self.X), float(self.Y)
        return FixedVector2(x * c - y * s, x * s + y * c)

    def to_tuple(self) -> tuple:
        """(x, y) as floats."""
        return float(self.X), float(self.Y)


# Shorter alias
Vector2 = FixedVector2


class StateBase(BaseModel):
    Position: FixedVector2


class BulletState(StateBase):
    Type: Literal["BulletState"] = "BulletState"
    Owner: str | None = None  # equipe du tireur ("Green" ou "Red"), comparer a ShipState.Team
    Radius: Fixed
    Velocity: FixedVector2


class MissileState(StateBase):
    Type: Literal["MissileState"] = "MissileState"
    Owner: str | None = None  # equipe du tireur ("Green" ou "Red"), comparer a ShipState.Team
    Radius: Fixed
    Rotation: Fixed = Fixed.ZERO
    Velocity: FixedVector2
    Armed: bool = True  # False tant que le missile n'a pas parcouru GameRules.MissileArmingDistance : un impact est sans effet


class BaseWallState(StateBase):
    Size: FixedVector2
    Rotation: Fixed  # radians


class WallState(BaseWallState):
    Type: Literal["WallState"] = "WallState"


class ShieldState(BaseWallState):
    Type: Literal["ShieldState"] = "ShieldState"
    Owner: str | None = None  # equipe du vaisseau qui l'a deploye ("Green" ou "Red")
    Velocity: FixedVector2
    Health: Fixed


class ShipState(StateBase):
    Type: Literal["ShipState"] = "ShipState"
    Team: str | None = None  # "Green" (depart a gauche) ou "Red" (depart a droite)
    Width: Fixed
    Height: Fixed
    Radius: Fixed = Fixed(0.419)  # collision radius
    Rotation: Fixed = Fixed.ZERO  # radians, direction the body faces
    Velocity: FixedVector2
    AimingDirection: FixedVector2 = Field(default_factory=lambda: FixedVector2(1, 0))  # unit vector
    Health: Fixed
    MaxHealth: Fixed = Fixed(1000)  # PV en debut de match (voir GameRules.ShipMaxHealth)
    ShieldCooldownFramesLeft: int
    MissileCooldownFramesLeft: int
    MachineGunCooldownFramesLeft: int
    DashCooldownFramesLeft: int


State = Annotated[
    Union[BulletState, MissileState, WallState, ShieldState, ShipState],
    Field(discriminator="Type"),
]


class GameState(BaseModel):
    SelfShip: ShipState | None = None
    EnemyShip: ShipState | None = None
    BulletStates: List[BulletState] = Field(default_factory=lambda: [])
    MissileStates: List[MissileState] = Field(default_factory=lambda: [])
    WallStates: List[WallState] = Field(default_factory=lambda: [])
    ShieldStates: List[ShieldState] = Field(default_factory=lambda: [])
    Done: bool
    # "Ongoing", "Win", "Loss" ou "Draw" (les deux vaisseaux detruits), de votre point de vue
    Outcome: Literal["Ongoing", "Win", "Loss", "Draw"] = "Ongoing"
    # Renseignes par le Headless d'entrainement uniquement (0 dans le jeu visuel) :
    Frame: int = 0  # frames simulees depuis le dernier reset
    DamageDealt: Fixed = Fixed.ZERO  # degats infliges au vaisseau adverse pendant le dernier step (toutes frames)
    DamageTaken: Fixed = Fixed.ZERO  # degats subis pendant le dernier step (toutes frames)
    # Demi-taille de la zone sure (GameRules.ZoneDamagePerSecond), centree sur l'arene : toute l'arene sans zone
    ZoneHalfSize: FixedVector2 = Field(default_factory=lambda: FixedVector2(ARENA_HALF_WIDTH, ARENA_HALF_HEIGHT))


# Valeurs par defaut des regles, celles du jeu (game/.../Core/Game/GameRules.cs) : les regles du hackathon.
DEFAULT_RULES = {
    "ShipMaxHealth": 1000.0,
    "BulletDamage": 60.0,
    "MissileDirectDamage": 600.0,
    "MissileAreaDamage": 400.0,
    "MissileGuided": True,
    "ShieldLifespan": 10.0,
    "MaxActiveShields": 0,
    "MatchDuration": 0.0,
    "MissileArmingDistance": 1.0,
    "ZoneDamagePerSecond": 50.0,
    "ZoneStart": 10.0,
    "ZoneShrinkDuration": 30.0,
    "ZoneFinalScale": 0.0,
}


class GameRules(BaseModel):
    """
    Regles d'un match (Headless d'entrainement), voir game/.../Core/Game/GameRules.cs.
    None = valeur par defaut du jeu (DEFAULT_RULES) : seules les regles renseignees sont envoyees.
    """

    model_config = ConfigDict(extra="forbid")  # une regle mal orthographiee leve une erreur

    ShipMaxHealth: Fixed | None = None  # PV d'un vaisseau en debut de match (1000)
    BulletDamage: Fixed | None = None  # degats d'une balle de mitrailleuse (60)
    MissileDirectDamage: Fixed | None = None  # degats d'un missile au vaisseau touche (600)
    MissileAreaDamage: Fixed | None = None  # degats de l'explosion d'un missile en zone (400)
    MissileGuided: bool | None = None  # le missile tourne vers la visee du tireur (true) ou va tout droit
    ShieldLifespan: Fixed | None = None  # duree de vie d'un bouclier, en secondes (10)
    MaxActiveShields: int | None = None  # boucliers simultanes par vaisseau, le plus ancien disparait (0 = sans limite)
    MatchDuration: Fixed | None = None  # duree d'un match en secondes, puis victoire aux PV (0 = sans limite)
    MissileArmingDistance: Fixed | None = None  # distance a parcourir avant qu'un missile soit arme (1) ; avant, un
                                                # impact le fait disparaitre sans degats
    ZoneDamagePerSecond: Fixed | None = None  # PV perdus par seconde hors de la zone sure (50 ; 0 = pas de zone)
    ZoneStart: Fixed | None = None  # instant (s) ou la zone commence a retrecir (10)
    ZoneShrinkDuration: Fixed | None = None  # duree (s) du retrecissement (30)
    ZoneFinalScale: Fixed | None = None  # taille finale de la zone, en fraction de l'arene (0 : elle se ferme)

    def value(self, name: str):
        """Valeur effective d'une regle : celle renseignee, sinon celle du jeu (DEFAULT_RULES)."""
        v = getattr(self, name)
        if v is None:
            return DEFAULT_RULES[name]
        return float(v) if isinstance(v, Fixed) else v


_GameRules = GameRules  # le champ Rules ci-dessous ne doit pas masquer la classe


class ResetOptions(BaseModel):
    """Options d'un reset (Headless d'entrainement). Les valeurs None gardent le comportement par defaut."""

    Seed: int | None = None  # graine de la carte : meme graine = meme carte. None = carte aleatoire
    MapDensity: float | None = None  # densite de murs (0 = aucun mur, 0.7 par defaut)
    SelfOnRight: bool = False  # votre vaisseau demarre a droite au lieu de la gauche
    Rules: _GameRules | None = None  # regles du match (None = regles par defaut)


class AIAction(BaseModel):
    """
    MovingDirection: vectors longer than 1 are normalized by the game, shorter ones make the ship move slower.
    AimingDirection: normalized by the game, a zero vector keeps the previous aiming direction.
    Both accept FixedVector2, (x, y) tuples, or dicts. Values are Fixed (floats/ints are converted).
    """

    MovingDirection: FixedVector2 = Field(default_factory=lambda: FixedVector2(1, 0))
    AimingDirection: FixedVector2 = Field(default_factory=lambda: FixedVector2(0, 1))
    MachineGun: bool = False
    Missile: bool = False
    Shield: bool = False
    Dash: bool = False


_ResetOptions = ResetOptions  # le champ ci-dessous porte le meme nom que la classe


class AIMessage(BaseModel):
    Reset: bool
    DoneWithTraining: bool
    SelfAction: AIAction | None = None
    EnemyAction: AIAction | None = None
    # Nombre de frames simulees avec ces actions (repetition d'action). Le jeu s'arrete plus tot si un vaisseau meurt.
    Frames: int = 1
    ResetOptions: _ResetOptions | None = None  # utilise seulement avec Reset=True


__all__ = [
    "Fixed", "FixedVector2", "Vector2", "StateBase", "BulletState", "MissileState", "BaseWallState", "WallState",
    "ShieldState", "ShipState", "State", "GameState", "GameRules", "ResetOptions", "AIAction", "AIMessage",
    "DECISION_INTERVAL", "ARENA_HALF_WIDTH", "ARENA_HALF_HEIGHT",
]
