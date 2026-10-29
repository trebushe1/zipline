"""Publish the emoji packs (data/emoji, see backend/emoji.py) as Telegram custom emoji sets, and keep them in step.

A set holds at most 200 emoji, so the heroes are split by the hero's first letter and the shop items get a set
of their own (SETS); inside a set the order is the pack's: each hero's head, then abilities 1–4; items by shop
tab, tier and name. The sets are made by a bot of their own
(TELEGRAM_EMOJI_BOT_TOKEN): Telegram ends a bot's set names in _by_<bot>, and the name is the public link, so
the notification bot never makes them. They belong to the user in TELEGRAM_CHAT_ID (the owner's private chat
with the notification bot: there the chat id is the user id), who has to press Start in the emoji bot once. A missing set is created; otherwise only what changed is changed:
a redrawn emoji is replaced in place, a new one added, one that left the set deleted, the order and the title
follow SETS. What is in each set is remembered in data/emoji/telegram.json, by emoji key (ability key, or
hero_<hero> for a head): checksum and Telegram's id.

Each emoji also carries an ordinary emoji (ALT): Telegram shows it where custom emoji are not drawn and
offers the custom one when it is typed; and keywords for search (the hero in English and Russian and the
ability, or the item in English and Russian and its shop tab).

    python -m backend.emoji_telegram --dry-run   # what would change; only reads from Telegram
    python -m backend.emoji_telegram             # create or update the sets
"""
import hashlib
import json
import re
import sys
import time

import httpx
from PIL import Image

from . import config
from .media import log

PACK = config.ROOT / "data" / "emoji"
STATE = PACK / "telegram.json"
# (heroes or items, first letters of the hero, set name, title). "_by_<bot username>" is added to the name here.
# A set's name is its link and can never change; its title follows this list.
SETS = [
    ("hero", "abcdefghijkl", "zipline_ability_al", "@zipline ability A–L"),
    ("hero", "mnopqrstuvwxyz", "zipline_ability_mz", "@zipline ability M–Z"),
    ("item", None, "zipline_items", "@zipline items"),
]
TAB_RU = {"weapon": "оружие", "vitality": "живучесть", "spirit": "дух"}  # as on the site's shop page
FIRST_BATCH = 50  # createNewStickerSet takes at most 50, the rest are added one by one
MAX_SET = 200  # Telegram's limit for a custom emoji set
KEYWORDS_LENGTH = 64  # Telegram's limit for all keywords of one emoji together

HERO_RU = {
    "atlas": "абрамс", "fencer": "аполло", "baba": "баба", "bebop": "бибоп", "punkgoat": "билли",
    "nano": "калико", "unicorn": "селеста", "deadpack": "дэнни", "drifter": "дрифтер", "dynamo": "динамо",
    "necro": "грейвс", "orion": "грей тэлон", "haze": "хейз", "astro": "холлидей", "inferno": "инфернус",
    "tengu": "айви", "kelvin": "кельвин", "ghost": "леди гейст", "lash": "лэш", "forge": "макгиннис",
    "vampirebat": "мина", "mirage": "мираж", "krill": "мо и крилл", "nurse": "харроу", "bookworm": "пейдж",
    "chrono": "парадокс", "synth": "покет", "ratking": "крысиный король", "familiar": "рем", "gigawatt": "севен",
    "shiv": "шив", "werewolf": "сильвер", "magician": "синклер", "chessmaster": "соломон", "doorman": "дорман",
    "priest": "венатор", "frank": "виктор", "hornet": "виндикта", "artist": "вайолет", "viscous": "вискоз",
    "viper": "вайпер", "warden": "варден", "wraith": "рейт", "yamato": "ямато",
}

# The ordinary emoji behind each custom one, by emoji key (stable when an ability is renamed).
ALT = {
    "hero_atlas": "🐂",                                      # Abrams
    "hero_fencer": "🤺",                                     # Apollo
    "hero_baba": "👵",                                       # Baba
    "hero_bebop": "🤖",                                      # Bebop
    "hero_punkgoat": "🐐",                                   # Billy
    "hero_nano": "🐈‍⬛",                                      # Calico
    "hero_unicorn": "🦄",                                    # Celeste
    "hero_deadpack": "💀",                                   # Deadman Danny
    "hero_doorman": "🛎️",                                    # The Doorman
    "hero_drifter": "🧛",                                    # Drifter
    "hero_dynamo": "🌌",                                     # Dynamo
    "hero_necro": "🪦",                                      # Graves
    "hero_orion": "🦉",                                      # Grey Talon
    "hero_haze": "🥷",                                       # Haze
    "hero_astro": "🤠",                                      # Holliday
    "hero_inferno": "🔥",                                    # Infernus
    "hero_tengu": "🗿",                                      # Ivy
    "hero_kelvin": "❄️",                                     # Kelvin
    "hero_ghost": "👻",                                      # Lady Geist
    "hero_lash": "💪",                                       # Lash
    "hero_forge": "🔧",                                      # McGinnis
    "hero_vampirebat": "🦇",                                 # Mina
    "hero_mirage": "🧞",                                     # Mirage
    "hero_krill": "🦡",                                      # Mo & Krill
    "hero_nurse": "🩺",                                      # Nurse Harrow
    "hero_bookworm": "📚",                                   # Paige
    "hero_chrono": "⏳",                                     # Paradox
    "hero_synth": "💼",                                      # Pocket
    "hero_ratking": "🐀",                                    # Rat King
    "hero_familiar": "😴",                                   # Rem
    "hero_gigawatt": "⚡",                                   # Seven
    "hero_shiv": "🔪",                                       # Shiv
    "hero_werewolf": "🐺",                                   # Silver
    "hero_magician": "🎩",                                   # Sinclair
    "hero_chessmaster": "♟️",                                # Solomon
    "hero_priest": "✝️",                                     # Venator
    "hero_frank": "🧟",                                      # Victor
    "hero_hornet": "🦅",                                     # Vindicta
    "hero_artist": "🎨",                                     # Violet
    "hero_viscous": "🟢",                                    # Viscous
    "hero_viper": "🐍",                                      # Vyper
    "hero_warden": "👮",                                     # Warden
    "hero_wraith": "🃏",                                     # Wraith
    "hero_yamato": "⚔️",                                     # Yamato
    # Items: by item key
    "upgrade_close_range": "🎯",                             # Close Quarters
    "upgrade_clip_size": "🔫",                               # Extended Magazine
    "upgrade_headshot_booster": "🎯",                        # Headshot Booster
    "upgrade_high_velocity_mag": "💨",                       # High-Velocity Rounds
    "upgrade_non_player_bonus": "👹",                        # Monster Rounds
    "upgrade_rapid_rounds": "⏩",                            # Rapid Rounds
    "upgrade_medic_bullets": "💉",                           # Restorative Shot
    "upgrade_active_reload": "🔄",                           # Active Reload
    "upgrade_fleetfoot_boots": "👟",                         # Fleetfoot
    "upgrade_intensifying_clip": "📈",                       # Intensifying Magazine
    "upgrade_kinetic_sash": "🐎",                            # Kinetic Dash
    "upgrade_long_range": "🔭",                              # Long Range
    "upgrade_melee_charge": "👊",                            # Melee Charge
    "upgrade_crackshot": "🔮",                               # Mystic Shot
    "upgrade_pristine_emblem": "💪",                         # Opening Rounds
    "upgrade_rechargingbullets": "⚡",                       # Recharging Rush
    "upgrade_slowing_bullets": "🐢",                         # Slowing Bullets
    "upgrade_tech_defense_shredders": "😈",                  # Spirit Shredder
    "upgrade_split_shot": "🔱",                              # Split Shot
    "upgrade_weapon_backstabber": "🥷",                      # Stalker
    "upgrade_blitz_bullets": "🦅",                           # Swift Striker
    "upgrade_titan_round": "🌾",                             # Titanic Magazine
    "upgrade_headshot_booster2": "🤕",                       # Weakening Headshot
    "upgrade_thermal_detonator": "🧪",                       # Alchemical Fire
    "upgrade_bulletshredimbue": "✨",                        # Ballistic Enchantment
    "upgrade_berserker": "🪓",                               # Berserker
    "upgrade_blood_tribute": "🩸",                           # Blood Tribute
    "upgrade_burst_fire": "🐍",                              # Burst Fire
    "upgrade_non_player_bonus_sacrifice": "📯",              # Cultist Sacrifice
    "upgrade_reinforcing_casings": "🛡️",                    # Escalating Resilience
    "upgrade_express_shot": "🌐",                            # Express Shot
    "upgrade_headhunter": "🎭",                              # Headhunter
    "upgrade_dps_aura": "👑",                                # Heroic Aura
    "upgrade_hollow_point_rounds": "💥",                     # Hollow Point
    "upgrade_bullet_armor_reduction_aura": "🐺",             # Hunter's Aura
    "upgrade_close_quarter_combat": "🔫",                    # Point Blank
    "upgrade_cloaking_device_active": "🐈‍⬛",                # Shadow Weave
    "upgrade_sharpshooter": "🎯",                            # Sharpshooter
    "upgrade_spellslinger_headshots": "💜",                  # Spirit Rend
    "upgrade_chain_lightning": "⚡",                         # Tesla Bullets
    "upgrade_toxic_bullets": "☠️",                          # Toxic Bullets
    "upgrade_weighted_shots": "🐘",                          # Weighted Shots
    "upgrade_aprounds": "🛡️",                               # Armor Piercer
    "upgrade_capacitor": "🔋",                               # Capacitor
    "upgrade_banshee_slugs": "💀",                           # Crippling Headshot
    "upgrade_crushing_fists": "👊",                          # Crushing Fists
    "upgrade_fervor": "😤",                                  # Frenzy
    "upgrade_glass_cannon": "💀",                            # Glass Cannon
    "upgrade_critshot": "🍀",                                # Lucky Shot
    "upgrade_ricochet": "⭐",                                # Ricochet
    "upgrade_proc_silence": "🤫",                            # Silencer
    "upgrade_enchanted_holsters": "👉",                      # Spellslinger
    "upgrade_tech_overflow": "🏆",                           # Spiritual Overflow
    "upgrade_health": "❤️",                                 # Extra Health
    "upgrade_endurance": "🩹",                               # Extra Regen
    "upgrade_improved_stamina": "🐎",                        # Extra Stamina
    "upgrade_grit": "🛡️",                                   # Grit
    "upgrade_health_stimpak": "🕯️",                         # Healing Rite
    "upgrade_lifestrike_gauntlets": "🤜",                    # Melee Lifesteal
    "upgrade_melee_rebuttal": "🐂",                          # Rebuttal
    "upgrade_sprint_booster": "👢",                          # Sprint Boots
    "upgrade_regenerating_bullet_shield": "🦺",              # Battle Vest
    "upgrade_vampire": "🧛",                                 # Bullet Lifesteal
    "upgrade_debuff_reducer": "⬇️",                         # Debuff Reducer
    "upgrade_magic_shield": "💠",                            # Enchanter's Emblem
    "upgrade_cardio_calibrator": "🏃",                       # Enduring Speed
    "upgrade_guardian_ward": "🗡️",                          # Guardian Ward
    "upgrade_healbane": "💀",                                # Healbane
    "upgrade_healing_booster": "💚",                         # Healing Booster
    "upgrade_vex_barrier": "🛡️",                            # Reactive Barrier
    "upgrade_restorative_locket": "📿",                      # Restorative Locket
    "upgrade_return_fire": "↩️",                            # Return Fire
    "upgrade_health_stealing_magic": "👁️",                  # Spirit Lifesteal
    "upgrade_spirit_bubble": "🫧",                           # Spirit Shielding
    "upgrade_trophy_collector": "🏆",                        # Trophy Collector
    "upgrade_weapon_shielding": "🛡️",                       # Weapon Shielding
    "upgrade_improved_bullet_armor": "🦾",                   # Bullet Resilience
    "upgrade_counterspell": "✋",                            # Counterspell
    "upgrade_reduce_debuff_duration": "✂️",                 # Dispel Magic
    "upgrade_chonky": "🦁",                                  # Fortitude
    "upgrade_fury_trance": "😡",                             # Fury Trance
    "upgrade_health_nova": "💚",                             # Healing Nova
    "upgrade_boxing_glove": "🥊",                            # Lifestrike
    "upgrade_rocket_booster": "🦘",                          # Majestic Leap
    "upgrade_metal_skin": "🤖",                              # Metal Skin
    "upgrade_rescue_beam": "🚑",                             # Rescue Beam
    "upgrade_tech_purge": "🧘",                              # Spirit Resilience
    "upgrade_superior_stamina": "🐎",                        # Stamina Mastery
    "upgrade_veil_walker": "🚶",                             # Veil Walker
    "upgrade_warp_stone": "💎",                              # Warp Stone
    "upgrade_cheat_death": "🃏",                             # Cheat Death
    "upgrade_colossus": "🗿",                                # Colossus
    "upgrade_divine_barrier": "🪽",                          # Divine Barrier
    "upgrade_diviners_kevlar": "🔰",                         # Diviner's Kevlar
    "upgrade_healbuff": "🥁",                                # Healing Tempo
    "upgrade_auto_cleanse": "⛓️",                           # Indomitable
    "upgrade_infuser": "🖐️",                                # Infuser
    "upgrade_inhibitor": "🚫",                               # Inhibitor
    "upgrade_juggernaut": "🐗",                              # Juggernaut
    "upgrade_damage_recycler": "🩸",                         # Leech
    "upgrade_phantom_strike": "👻",                          # Phantom Strike
    "upgrade_deflecting_armor": "🦺",                        # Plated Armor
    "upgrade_siphon_bullets": "🌹",                          # Siphon Bullets
    "upgrade_spellbreaker": "🪄",                            # Spellbreaker
    "upgrade_unstoppable": "🧍",                             # Unstoppable
    "upgrade_surging_power": "🦇",                           # Vampiric Burst
    "upgrade_absorbing_armor": "🧙",                         # Witchmail
    "upgrade_extra_charge": "🔋",                            # Extra Charge
    "upgrade_improved_spirit": "🔥",                         # Extra Spirit
    "upgrade_goose_egg": "🥚",                               # Golden Goose Egg
    "upgrade_magic_burst": "✴️",                            # Mystic Burst
    "upgrade_magic_reach": "↔️",                            # Mystic Expansion
    "upgrade_mystic_regeneration": "💜",                     # Mystic Regeneration
    "upgrade_withering_whip": "🔫",                          # Rusted Barrel
    "upgrade_acolytes_glove": "🥊",                          # Spirit Strike
    "upgrade_arcane_surge": "🦄",                            # Arcane Surge
    "upgrade_bullet_resist_shredder": "🛡️",                 # Bullet Resist Shredder
    "upgrade_cold_front": "❄️",                             # Cold Front
    "upgrade_magic_tempo": "⏱️",                            # Compress Cooldown
    "upgrade_arcane_extension": "⏳",                        # Duration Extender
    "upgrade_soaring_spirit": "🔥",                          # Improved Spirit
    "upgrade_magic_slow": "⛓️",                             # Mystic Slow
    "upgrade_magic_vulnerability": "👁️",                    # Mystic Vulnerability
    "upgrade_quick_silver": "🔄",                            # Quicksilver Reload
    "upgrade_containment": "🐌",                             # Slowing Hex
    "upgrade_spirit_sap": "💧",                              # Spirit Sap
    "upgrade_suppressor": "🔗",                              # Suppressor
    "upgrade_rupture": "🦴",                                 # Decay
    "upgrade_greater_withering_whip": "🖐️",                 # Disarming Hex
    "upgrade_tech_range": "📡",                              # Greater Expansion
    "upgrade_target_stun": "🔨",                             # Knockdown
    "upgrade_resonant_healing": "💚",                        # Radiant Regeneration
    "upgrade_rapid_recharge": "⚡",                          # Rapid Recharge
    "upgrade_targeted_silence": "🔇",                        # Silence Wave
    "upgrade_spirit_snatch": "🌀",                           # Spirit Snatch
    "upgrade_cooldown_reduction": "⏱️",                     # Superior Cooldown
    "upgrade_imbued_duration_extender": "⌛",                # Superior Duration
    "upgrade_magic_storm": "🌩️",                            # Surge of Power
    "upgrade_magic_shock": "💣",                             # Tankbuster
    "upgrade_tech_damage_pulse": "💓",                       # Torment Pulse
    "upgrade_arctic_blast": "🥶",                            # Arctic Blast
    "upgrade_boundless_spirit": "💫",                        # Boundless Spirit
    "upgrade_glitch": "🗿",                                  # Cursed Relic
    "upgrade_ability_power_shard": "🔷",                     # Echo Shard
    "upgrade_escalating_exposure": "☢️",                    # Escalating Exposure
    "upgrade_self_bubble": "🌫️",                            # Ethereal Shift
    "upgrade_focus_lens": "🔍",                              # Focus Lens
    "upgrade_ultimate_burst": "📜",                          # Lightning Scroll
    "upgrade_magic_carpet": "🧞",                            # Magic Carpet
    "upgrade_ethereal_bullets": "🔫",                        # Mercurial Magnum
    "upgrade_mystic_reverb": "〰️",                          # Mystic Reverb
    "upgrade_ability_refresher": "♻️",                      # Refresher
    "upgrade_discord": "😈",                                 # Scourge
    "upgrade_spirit_burn": "🔥",                             # Spirit Burn
    "upgrade_transcendent_cooldown": "🌌",                   # Transcendent Cooldown
    "upgrade_aoe_root": "🕸️",                               # Vortex Web
    "citadel_ability_bull_heal": "🩸",                       # Abrams: Siphon Life
    "citadel_ability_bull_charge": "🐂",                     # Abrams: Shoulder Charge
    "citadel_ability_passive_beefy": "❤️‍🔥",                # Abrams: Infernal Resilience
    "citadel_ability_bull_leap": "💥",                       # Abrams: Seismic Impact
    "ability_fencer_throwblade": "✴️",                      # Apollo: Disengaging Sigil
    "ability_fencer_riposte": "⚔️",                         # Apollo: Riposte
    "ability_fencer_lunge": "🤺",                            # Apollo: Flawless Advance
    "ability_fencer_ultimate": "🗡️",                        # Apollo: Itani Lo Sahn
    "ability_baba_bubbling_brew": "🧵",                      # Baba: Threadsap
    "ability_baba_bench_run": "🦵",                          # Baba: Granny Long Legs
    "ability_baba_hexing_brew": "🫖",                        # Baba: Baba's Brew
    "ability_baba_ultimate_2": "🐦",                         # Baba: Feed The Birds
    "citadel_ability_uppercut": "👊",                        # Bebop: Exploding Uppercut
    "citadel_ability_sticky_bomb": "💣",                     # Bebop: Sticky Bomb
    "citadel_ability_hook": "🦾",                            # Bebop: Grapple Arm
    "citadel_ability_bebop_laser_beam": "🔆",                # Bebop: Hyper Beam
    "ability_punkgoat_ult": "🏏",                            # Billy: Bashdown
    "ability_punkgoat_goatflip": "🐐",                       # Billy: Rising Ram
    "ability_punkgoat_blasted": "💥",                        # Billy: Blasted
    "ability_punkgoat_tether": "⛓️",                        # Billy: Chain Gang
    "ability_nano_clustergrenade": "💣",                     # Calico: Gloom Bombs
    "ability_nano_dash": "🐾",                               # Calico: Leaping Slash
    "ability_nano_catform": "🐈‍⬛",                          # Calico: Ava
    "ability_nano_shadow_pulse": "🌑",                       # Calico: Return to Shadows
    "ability_unicorn_radiantblast": "🦄",                    # Celeste: Light Eater
    "ability_unicorn_prismaticguard": "✨",                  # Celeste: Dazzling Trick
    "ability_unicorn_luminousstrike": "🗡️",                 # Celeste: Radiant Daggers
    "ability_unicorn_dazzlingorb": "🌟",                     # Celeste: Shining Wonder
    "ability_deadpack_chargeblast": "🚀",                    # Deadman Danny: Strix Salvo
    "ability_deadpack_rocketeer": "🔫",                      # Deadman Danny: Chimera Mk1
    "ability_deadpack_flamethrower": "⛽",                   # Deadman Danny: Jettison Fuel
    "ability_deadpack_ultimate": "🤝",                       # Deadman Danny: Close The Deal
    "drifter_blood_blast": "🔪",                             # Drifter: Rend
    "drifter_shadow_mark": "🎯",                             # Drifter: Stalker's Mark
    "ability_drifter_hunger": "🧛",                          # Drifter: Bloodscent
    "drifter_darkness": "🌘",                                # Drifter: Eternal Night
    "citadel_ability_stomp": "💫",                           # Dynamo: Kinetic Pulse
    "citadel_ability_void_sphere": "🌌",                     # Dynamo: Quantum Entanglement
    "citadel_ability_nikuman": "💚",                         # Dynamo: Rejuvenating Aurora
    "citadel_ability_self_vacuum": "🌀",                     # Dynamo: Singularity
    "ability_necro_hauntingskull": "⚱️",                    # Graves: Jar of Dead
    "ability_necro_zombiewall": "🧟",                        # Graves: Grasping Hands
    "ability_necro_fear": "👻",                              # Graves: Essence Theft
    "ability_necro_gravestone": "🪦",                        # Graves: Borrowed Decree
    "ability_charged_shot": "🏹",                            # Grey Talon: Charged Shot
    "ability_power_jump": "🌧️",                             # Grey Talon: Rain of Arrows
    "ability_immobilize_trap": "🪤",                         # Grey Talon: Spirit Snare
    "ability_guided_arrow": "🦉",                            # Grey Talon: Guided Owl
    "ability_sleep_dagger": "😴",                            # Haze: Sleep Dagger
    "ability_smoke_bomb": "💨",                              # Haze: Smoke Bomb
    "ability_stacking_damage": "🎯",                         # Haze: Fixation
    "ability_bullet_flurry": "🔫",                           # Haze: Bullet Dance
    "ability_explosive_barrel": "🛢️",                       # Holliday: Powder Keg
    "ability_bounce_pad": "🆙",                              # Holliday: Bounce Pad
    "ability_crackshot": "🎯",                               # Holliday: Crackshot
    "ability_gravity_lasso": "🤠",                           # Holliday: Spirit Lasso
    "ability_incendiary_projectile": "🧨",                   # Infernus: Napalm
    "ability_flame_dash": "🏃",                              # Infernus: Flame Dash
    "ability_afterburn": "🔥",                               # Infernus: Afterburn
    "ability_fire_bomb": "💥",                               # Infernus: Concussive Combustion
    "citadel_ability_tengu_urn": "🌿",                       # Ivy: Entangling Thorns
    "citadel_ability_tangotether": "🔗",                     # Ivy: Kudzu Connection
    "citadel_ability_tengu_stone_form": "🗿",                # Ivy: Stone Form
    "citadel_ability_tengu_airlift": "🪂",                   # Ivy: Air Drop
    "ability_ice_grenade": "❄️",                            # Kelvin: Frost Grenade
    "ability_icepath": "⛸️",                                # Kelvin: Ice Path
    "ability_icebeam": "🌬️",                                # Kelvin: Arctic Beam
    "ability_ice_dome": "🧊",                                # Kelvin: Frozen Shelter
    "ability_blood_bomb": "🩸",                              # Lady Geist: Essence Bomb
    "ability_life_drain": "💔",                              # Lady Geist: Life Drain
    "ability_blood_shards": "🗡️",                           # Lady Geist: Malice
    "ability_health_swap": "🔄",                             # Lady Geist: Soul Exchange
    "citadel_ability_lash_down_strike": "⬇️",               # Lash: Ground Strike
    "citadel_ability_lash": "🪝",                            # Lash: Grapple
    "ability_lash_flog": "🪢",                               # Lash: Flog
    "citadel_ability_lash_ultimate": "💀",                   # Lash: Death Slam
    "citadel_ability_shieldedsentry": "🤖",                  # McGinnis: Mini Turret
    "citadel_ability_mobile_resupply": "💊",                 # McGinnis: Medicinal Specter
    "citadel_ability_fissure_wall": "🧱",                    # McGinnis: Spectral Wall
    "citadel_ability_rocket_barrage": "🚀",                  # McGinnis: Heavy Barrage
    "ability_vampirebat_steallife": "🩸",                    # Mina: Rake
    "ability_vampirebat_batblink": "🦇",                     # Mina: Sanguine Retreat
    "ability_vampirebat_lovebites": "💋",                    # Mina: Love Bites
    "ability_vampirebat_batswarm": "🌀",                     # Mina: Nox Nostra
    "mirage_fire_beetles": "🪲",                             # Mirage: Fire Scarabs
    "mirage_tornado": "🌪️",                                 # Mirage: Dust Devil
    "mirage_sand_phantom": "👁️",                            # Mirage: Djinn's Mark
    "mirage_teleport": "🧞",                                 # Mirage: Traveler
    "ability_intimidate": "💢",                              # Mo & Krill: Scorn
    "ability_burrow": "🕳️",                                 # Mo & Krill: Burrow
    "ability_throw_sand": "🏜️",                             # Mo & Krill: Sand Blast
    "ability_ult_combo": "🥊",                               # Mo & Krill: Combo
    "ability_nurse_leech_therapy": "🪱",                     # Nurse Harrow: Leech Therapy
    "ability_nurse_adrenaline_rush": "💨",                   # Nurse Harrow: Wretched Vapors
    "ability_nurse_healing_needles": "💉",                   # Nurse Harrow: Healing Needles
    "ability_nurse_fear": "👹",                              # Nurse Harrow: Lurking Terrors
    "ability_bookworm_dragonfire": "🐉",                     # Paige: Bookwyrm
    "ability_bookworm_knightbarrier": "🛡️",                 # Paige: Plot Armor
    "ability_bookworm_aoemagic": "📖",                       # Paige: Captivating Read
    "ability_bookworm_knightcharge": "🐴",                   # Paige: Rallying Charge
    "citadel_ability_chrono_pulse_grenade": "⏳",            # Paradox: Pulse Grenade
    "citadel_ability_chrono_time_wall": "⌛",                # Paradox: Time Wall
    "citadel_ability_chrono_kinetic_carbine": "🔫",          # Paradox: Kinetic Carbine
    "citadel_ability_chrono_swap": "🔄",                     # Paradox: Paradoxical Swap
    "synth_barrage": "☄️",                                  # Pocket: Barrage
    "synth_plasma_flux": "🧥",                               # Pocket: Flying Cloak
    "synth_pulse": "💼",                                     # Pocket: Enchanter's Satchel
    "synth_affliction": "🐸",                                # Pocket: Affliction
    "ability_ratking_scrap_grenade": "💣",                   # Rat King: Scrap Grenade
    "ability_ratking_ratnibble": "🐀",                       # Rat King: Rat Swarm
    "ability_ratking_ratarmor": "👑",                        # Rat King: Royal Pestments
    "ability_ratking_standard_bearer": "🚩",                 # Rat King: Rule, Ratannia!
    "ability_familiar_ability02": "💤",                      # Rem: Pillow Toss
    "ability_familiar_attach": "🫂",                         # Rem: Tag Along
    "ability_familiar_helpinghands": "👻",                   # Rem: Lil Helpers
    "ability_familiar_ability01": "🌙",                      # Rem: Naptime
    "citadel_ability_lightning_ball": "⚡",                  # Seven: Lightning Ball
    "citadel_ability_static_charge": "🧲",                   # Seven: Static Charge
    "ability_power_surge": "🔋",                             # Seven: Power Surge
    "citadel_ability_storm_cloud": "⛈️",                    # Seven: Storm Cloud
    "citadel_ability_shiv_dagger": "🔪",                     # Shiv: Serrated Knives
    "citadel_ability_shiv_dash": "🗡️",                      # Shiv: Slice and Dice
    "citadel_ability_shiv_defer_damage": "🩸",               # Shiv: Bloodletting
    "citadel_ability_shiv_killing_blow": "☠️",              # Shiv: Killing Blow
    "ability_werewolf_unloadgun": "🔫",                      # Silver: Slam Fire
    "ability_werewolf_kickflip": "🥾",                       # Silver: Boot Kick
    "ability_werewolf_netshot": "🪢",                        # Silver: Entangling Bola
    "ability_werewolf_transformation": "🐺",                 # Silver: Lycan Curse
    "ability_magician_magicbolt": "✨",                      # Sinclair: Vexing Bolt
    "ability_magician_cloneturret": "💃",                    # Sinclair: Spectral Assistant
    "ability_magician_animalhexarea": "🐇",                  # Sinclair: Rabbit Hex
    "ability_magician_copyult": "🎩",                        # Sinclair: Audience Participation
    "ability_chessmaster_throwbishop": "♟️",                # Solomon: Setup Bishop
    "ability_chessmaster_knightbuff": "🐴",                  # Solomon: Kingside Knight
    "ability_chessmaster_forkpiece": "🏰",                   # Solomon: Rook Battery
    "ability_chessmaster_queen": "👑",                       # Solomon: Develop Queen
    "ability_doorman_bomb": "🛎️",                           # The Doorman: Call Bell
    "ability_doorman_doorway": "🚪",                         # The Doorman: Doorway
    "ability_doorman_luggage_cart": "🧳",                    # The Doorman: Luggage Cart
    "ability_doorman_hotel": "🏨",                           # The Doorman: Hotel Guest
    "ability_priest_flashbang": "🧪",                        # Venator: Consecrating Grenade
    "ability_priest_knockback": "💥",                        # Venator: Gutshot
    "ability_priest_beartrap": "🪤",                         # Venator: Hex-Lined Snap Trap
    "ability_priest_weaponswap": "✝️",                      # Venator: Ira Domini
    "ability_frank_shocktarget2": "🔋",                      # Victor: Pain Battery
    "ability_frank_selfzap": "⚡",                           # Victor: Jumpstart
    "ability_frank_painaura": "😖",                          # Victor: Aura of Suffering
    "ability_frank_revive": "🧟",                            # Victor: Shocking Reanimation
    "citadel_ability_hornet_chain": "📍",                    # Vindicta: Stake
    "citadel_ability_hornet_leap": "🕊️",                    # Vindicta: Flight
    "citadel_ability_hornet_sting": "🐦",                    # Vindicta: Crow Familiar
    "citadel_ability_hornet_snipe": "🎯",                    # Vindicta: Assassinate
    "ability_artist_homingbrush": "🖋️",                     # Violet: Living Ink
    "ability_artist_rollingprojectile": "🖌️",               # Violet: Flow State
    "ability_artist_tether": "🖼️",                          # Violet: Still Life
    "ability_artist_bamboogrove": "🎨",                      # Violet: Abstract Landscape
    "viscous_goo_grenade": "💦",                             # Viscous: Splatter
    "viscous_restorative_goo": "🧊",                         # Viscous: The Cube
    "viscous_telepunch": "👊",                               # Viscous: Puddle Punch
    "viscous_goo_bowling_ball": "🟢",                        # Viscous: Goo Ball
    "ability_viper_debuffdagger": "🗡️",                     # Vyper: Screwjab Dagger
    "ability_viper_venom": "🐍",                             # Vyper: Lethal Venom
    "ability_viper_snakedash": "💨",                         # Vyper: Slither
    "ability_viper_petrifybola": "🗿",                       # Vyper: Petrifying Bola
    "ability_warden_crowd_control": "🧪",                    # Warden: Alchemical Flask
    "ability_warden_high_alert": "🛡️",                      # Warden: Willpower
    "ability_warden_lock_down": "⛓️",                       # Warden: Binding Word
    "ability_warden_riot_protocol": "🚨",                    # Warden: Last Stand
    "citadel_ability_card_toss": "🃏",                       # Wraith: Card Trick
    "citadel_ability_projectmind": "🌀",                     # Wraith: Project Mind
    "citadel_ability_wraith_rapidfire": "🔫",                # Wraith: Full Auto
    "citadel_ability_psychic_lift": "✋",                    # Wraith: Telekinesis
    "citadel_ability_power_slash": "⚔️",                    # Yamato: Power Slash
    "citadel_ability_flying_strike": "🗡️",                  # Yamato: Flying Slash
    "citadel_ability_healing_slash": "🩸",                   # Yamato: Crimson Slash
    "citadel_ability_infinity_slash": "🌑",                  # Yamato: Shadow Transformation
}
NO_ALT = "🎮"  # a new hero, ability or item before it gets its own emoji here


class TelegramError(Exception):
    pass


class Bot:
    def __init__(self, token: str):
        self.api = f"https://api.telegram.org/bot{token}/"
        self.http = httpx.Client(timeout=120)

    def call(self, method: str, data: dict | None = None, files: dict | None = None):
        for attempt in range(6):
            r = self.http.post(self.api + method, data=data, files=files).json()
            if r.get("ok"):
                return r["result"]
            wait = (r.get("parameters") or {}).get("retry_after")
            if not wait or attempt == 5:
                raise TelegramError(f"{method}: {r.get('description')}")
            log(f"Telegram asks to wait {wait} s")
            time.sleep(wait + 1)

    def sticker_set(self, name: str) -> dict | None:
        try:
            return self.call("getStickerSet", {"name": name})
        except TelegramError as e:
            if "STICKERSET_INVALID" in str(e):
                return None
            raise


# ── One emoji ──────────────────────────────────────────

def checksum(entry: dict) -> str:
    return hashlib.sha256((PACK / entry["file"]).read_bytes()).hexdigest()[:16]


def alt(entry: dict) -> list[str]:
    return [ALT.get(entry["key"], NO_ALT)]


def keywords(entry: dict) -> list[str]:
    if "item" in entry:
        words = (entry["name"].lower(), entry.get("name_ru", "").lower(), TAB_RU.get(entry["tab"], ""))
    else:
        words = (entry["hero_name"].lower(), HERO_RU.get(entry["hero"], ""), entry["name"].lower())
    out, total = [], 0
    for k in words:
        if k and k not in out and total + len(k) <= KEYWORDS_LENGTH:
            out.append(k)
            total += len(k)
    return out


def upload(entry: dict, attach: str) -> tuple[dict, tuple]:
    """The InputSticker and its file part."""
    sticker = {"sticker": f"attach://{attach}", "format": "static", "emoji_list": alt(entry), "keywords": keywords(entry)}
    return sticker, (entry["file"], (PACK / entry["file"]).read_bytes(), "image/png")


def record(entry: dict, uid: str) -> dict:
    return {"file": entry["file"], "sha": checksum(entry), "id": uid, "alt": alt(entry), "keywords": keywords(entry)}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", "utf-8")


# ── Sync ───────────────────────────────────────────────

def set_of(entry: dict) -> int:
    """Which of SETS a pack entry goes to: items to the items set, a hero by its first letter (The Doorman under D)."""
    kind = "item" if "item" in entry else "hero"
    letter = re.sub(r"^the ", "", entry.get("hero_name", "").lower())[:1]
    fits = [i for i, (k, letters, _, _) in enumerate(SETS) if k == kind and (letters is None or letter in letters)]
    return fits[0] if fits else max(i for i, (k, *_) in enumerate(SETS) if k == kind)


def plan(pack: list[dict], known: dict, current: dict) -> list[tuple[str, dict | str]]:
    """What to do so the set holds exactly `pack`: (op, entry) or ("delete", emoji key)."""
    in_set = {s["file_unique_id"] for s in current["stickers"]}
    wanted = {e["key"] for e in pack}
    ops = [("delete", key) for key, k in known.items() if key not in wanted and k["id"] in in_set]
    for e in pack:
        k = known.get(e["key"])
        if not k or k["id"] not in in_set:
            ops.append(("add", e))
        elif k["sha"] != checksum(e):
            ops.append(("replace", e))
        elif k["alt"] != alt(e) or k["keywords"] != keywords(e):
            ops.append(("retag", e))
    return ops


def run(bot: Bot, owner: str, name: str, pack: list[dict], state: dict, known: dict) -> None:
    """Bring set `name` to `pack`; `known` is its part of `state`."""

    def file_id(uid: str) -> str:
        return next(s["file_id"] for s in bot.sticker_set(name)["stickers"] if s["file_unique_id"] == uid)

    def put(method: str, e: dict, **extra) -> None:
        """Add or replace one emoji and learn its new id: the one sticker that was not there before."""
        before = {s["file_unique_id"] for s in bot.sticker_set(name)["stickers"]}
        sticker, part = upload(e, "e")
        bot.call(method, {"user_id": owner, "name": name, "sticker": json.dumps(sticker, ensure_ascii=False), **extra}, {"e": part})
        new = [s["file_unique_id"] for s in bot.sticker_set(name)["stickers"] if s["file_unique_id"] not in before]
        known[e["key"]] = record(e, new[0])
        save(state)

    for op, e in plan(pack, known, bot.sticker_set(name)):
        if op == "delete":
            log(f"  delete {known[e]['file']}")
            bot.call("deleteStickerFromSet", {"sticker": file_id(known.pop(e)["id"])})
            save(state)
        elif op == "add":
            log(f"  add {e['file']}")
            put("addStickerToSet", e)
        elif op == "replace":
            log(f"  replace {e['file']}")
            put("replaceStickerInSet", e, old_sticker=file_id(known[e["key"]]["id"]))
        else:
            log(f"  retag {e['file']}")
            fid = file_id(known[e["key"]]["id"])
            bot.call("setStickerEmojiList", {"sticker": fid, "emoji_list": json.dumps(alt(e), ensure_ascii=False)})
            bot.call("setStickerKeywords", {"sticker": fid, "keywords": json.dumps(keywords(e), ensure_ascii=False)})
            known[e["key"]] = record(e, known[e["key"]]["id"])
            save(state)
    for key in set(known) - {e["key"] for e in pack}:  # gone from Telegram some other way
        del known[key]

    # Order: the pack's, stickers added by hand stay after it.
    stickers = bot.sticker_set(name)["stickers"]
    ids, fids = [s["file_unique_id"] for s in stickers], {s["file_unique_id"]: s["file_id"] for s in stickers}
    for pos, e in enumerate(pack):
        uid = known[e["key"]]["id"]
        if ids[pos] != uid:
            bot.call("setStickerPositionInSet", {"sticker": fids[uid], "position": pos})
            ids.remove(uid)
            ids.insert(pos, uid)


def create(bot: Bot, owner: str, name: str, title: str, pack: list[dict], state: dict, known: dict) -> None:
    """A new set from the first FIRST_BATCH emoji; run() adds the rest."""
    first = pack[:FIRST_BATCH]
    parts = [upload(e, f"e{i}") for i, e in enumerate(first)]
    try:
        bot.call("createNewStickerSet", {
            "user_id": owner, "name": name, "title": title, "sticker_type": "custom_emoji",
            "stickers": json.dumps([s for s, _ in parts], ensure_ascii=False),
        }, {f"e{i}": part for i, (_, part) in enumerate(parts)})
    except TelegramError as e:
        if "PEER_ID_INVALID" in str(e) or "user not found" in str(e).lower():
            raise TelegramError(f"{e}: the owner has to press Start in @{name.rsplit('_by_', 1)[1]} first") from e
        raise
    stickers = bot.sticker_set(name)["stickers"]
    known.update({e["key"]: record(e, s["file_unique_id"]) for e, s in zip(first, stickers)})
    save(state)


# ── Main ───────────────────────────────────────────────

def problems(parts: list[list[dict]]) -> list[str]:
    out = [f"{SETS[i][3]}: {len(p)} emoji, Telegram takes at most {MAX_SET} in one set: split SETS further"
           for i, p in enumerate(parts) if len(p) > MAX_SET]
    for e in (e for p in parts for e in p):
        with Image.open(PACK / e["file"]) as im:
            if im.size != (100, 100):
                out.append(f"{e['file']} is {im.size[0]}×{im.size[1]}, must be 100×100")
    return out


def load_state() -> dict:
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    if "set" in state:  # one set, before the pack was split
        state = {"sets": {state["set"]: {"title": state.get("title"), "emoji": state.get("emoji", {})}}}
    state.setdefault("sets", {})
    return state


def summary(ops: list, known: dict) -> str:
    groups = {}
    for op, e in ops:
        groups.setdefault(op, []).append(known[e]["file"] if op == "delete" else e["file"])
    return "\n".join(f"  {op} {len(files)}: " + ", ".join(files[:6]) + (", …" if len(files) > 6 else "")
                     for op, files in groups.items())


def main() -> int:
    dry = "--dry-run" in sys.argv
    token, owner = config.TELEGRAM_EMOJI_BOT_TOKEN, config.TELEGRAM_CHAT_ID
    if not token:
        log("TELEGRAM_EMOJI_BOT_TOKEN is not set: make a bot for the emoji sets in @BotFather and put its token in .env")
        return 1
    if token == config.TELEGRAM_BOT_TOKEN:
        log("TELEGRAM_EMOJI_BOT_TOKEN is the notification bot: its name would be in the sets' public links")
        return 1
    if not owner.isdigit():
        log("TELEGRAM_CHAT_ID must be the owner's private chat with the bot (a positive id): the sets belong to that user")
        return 1
    pack = json.loads((PACK / "index.json").read_text("utf-8"))["emoji"]
    parts = [[e for e in pack if set_of(e) == i] for i in range(len(SETS))]
    if issues := problems(parts):
        log("the pack cannot go to Telegram:\n  " + "\n  ".join(issues))
        return 1
    for e in pack:
        if e["key"] not in ALT:
            log(f"no ordinary emoji for {e['hero_name']}: {e['name']} ({e['key']}): {NO_ALT} until it is added to ALT")

    bot = Bot(token)
    bot_name = bot.call("getMe")["username"]
    state = load_state()
    for (_, _, base, title), part in zip(SETS, parts):
        name = f"{base}_by_{bot_name}"
        mine = state["sets"].setdefault(name, {"title": title, "emoji": {}})
        known = mine["emoji"]
        current = bot.sticker_set(name)
        if current is None:
            log(f"{name}: not in Telegram yet; {'would create' if dry else 'creating'} \"{title}\" with {len(part)} emoji")
            if dry:
                continue
            known.clear()
            create(bot, owner, name, title, part, state, known)
        else:
            ops = plan(part, known, current)
            ids = {k["id"] for k in known.values()}
            foreign = sum(1 for s in current["stickers"] if s["file_unique_id"] not in ids)
            retitle = current["title"] != title
            log(f"{name}: {len(current['stickers'])} emoji in Telegram, {len(part)} in the pack"
                + (f" ({foreign} not from the pack, left alone)" if foreign else "")
                + f"; {len(ops) or 'nothing'} to change" + (f", title \"{current['title']}\" → \"{title}\"" if retitle else ""))
            if ops:
                log(summary(ops, known))
            if dry:
                continue
            if retitle:
                bot.call("setStickerSetTitle", {"name": name, "title": title})
        mine["title"] = title
        run(bot, owner, name, part, state, known)
        save(state)
        log(f"done: https://t.me/addemoji/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
