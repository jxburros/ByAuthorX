"""Rebuild ``tests/fixtures/entailment/pairs.json``, the labelled claim/source set for the entailment stage.

Maintainer tool; nothing in the application runs it. Every pair was written and labelled by hand
(context engine plan 9: "agreement with human labels is reported before it ships"):

* ``supported``   the source states what the claim says (a paraphrase or a direct consequence).
* ``partial``     at least one substantive part of the claim is stated by the source and another part is not
                  (extra detail, a stronger or wider statement, or a part the source contradicts).
* ``unsupported`` no substantive part of the claim is stated by the source (it is absent or contradicted),
                  even when the claim is true in the world or elsewhere in the same book.

Sources are three kinds of text a writer actually cites: ``pp`` paragraphs of *Pride and Prejudice*
(chapters I-VI, public domain, taken from ``tests/fixtures/codex/pride-and-prejudice-1-6.json``), ``novel``
paragraphs of the synthetic manuscript in ``tests/fixtures/router/novel.json``, and ``research`` passages
written for this fixture in the register of encyclopaedia and report prose (invented; any resemblance to a
real catalogue or survey is incidental). Pair ``id`` order is fixed so a split by parity is stable:
odd ids are the development half (thresholds are chosen there), even ids the held-out half.

    uv run python scripts/build_entailment_fixture.py            # rewrite the fixture
    uv run python scripts/build_entailment_fixture.py --check    # fail if the committed file is stale
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tests' / 'fixtures' / 'entailment' / 'pairs.json'
PP = ROOT / 'tests' / 'fixtures' / 'codex' / 'pride-and-prejudice-1-6.json'
NOVEL = ROOT / 'tests' / 'fixtures' / 'router' / 'novel.json'

RESEARCH = {
    'R1': (
        'Early lighthouses burned wood or coal in open braziers on the summit of a tower. The brazier’s '
        'light was weak and its smoke dimmed it in damp weather, so keepers often spent the night feeding '
        'the fire. In 1822 the French engineer Augustin Fresnel proposed a lens built from concentric rings '
        'of glass prisms, which captured far more of the lamp’s light and bent it into a narrow horizontal '
        'beam. The first Fresnel lens was installed at the Cordouan lighthouse in 1823. Fresnel lenses were '
        'graded into orders by size, the first order being the largest, and a first-order lens could be '
        'seen from more than twenty miles away in clear weather.'
    ),
    'R2': (
        'The herring gull colony on the island was surveyed in June of three consecutive years. Observers '
        'counted 412 occupied nests in the first year, 389 in the second and 301 in the third. Most nests '
        'were on bare ground above the high-tide line, though a small number were built among the ruins of '
        'the old signal station. Chick survival was lowest in the third year, when a rat was seen near the '
        'northern cliffs. The authors caution that the survey cannot show whether the rat caused the '
        'decline, because food supply was not measured.'
    ),
    'R3': (
        'The monastery library at Saint-Aure held about nine hundred volumes in 1340, most of them copied '
        'in its own scriptorium. A catalogue made that year lists the books by shelf, and notes that '
        'borrowing was allowed only with the abbot’s written permission. Two volumes were recorded as '
        'missing. The catalogue’s compiler, Brother Anselm, added that damp had spoiled the lower shelves '
        'of the north wall, and that the books stored there had been moved upstairs.'
    ),
    'R4': (
        'Arabica coffee grows best at altitudes between 1,000 and 2,000 metres, where nights are cool and '
        'rainfall is steady. Trees begin bearing fruit about three years after planting and yield their '
        'largest harvests between years five and twenty. Cherries ripen unevenly, so on many farms pickers '
        'return to the same tree several times during the season. After picking, the fruit is either dried '
        'whole or pulped and fermented before drying; the choice affects flavour but not the number of '
        'beans in each cherry, which is usually two.'
    ),
    'R5': (
        'In winter, honey bees do not hibernate. The workers form a cluster around the queen and shiver '
        'their flight muscles to generate heat, keeping the centre of the cluster near 30 degrees Celsius '
        'even when the air outside is below freezing. Bees on the outside of the cluster rotate inward at '
        'intervals so that no individual stays in the cold for long. The colony survives on stored honey, '
        'and a hive that runs out of stores before spring will starve even if the temperature is mild.'
    ),
    'R6': (
        'Spring tides occur twice each lunar month, at new moon and full moon, when the sun, moon and earth '
        'are roughly aligned and their gravitational effects combine. They produce the highest high tides '
        'and the lowest low tides. Neap tides occur in between, at the first and last quarter moons, when '
        'the sun and moon pull at right angles and the tidal range is smallest. The name “spring” has '
        'nothing to do with the season; it comes from an old sense of the word meaning to leap or rise.'
    ),
    'R7': (
        'The harbour board approved dredging of the main channel in March 1894, at an estimated cost of '
        '14,000 pounds. Work began in September and was completed the following May, two months behind '
        'schedule because of winter storms. The channel was deepened from 14 to 19 feet at low water, '
        'allowing steamers of up to 1,800 tons to reach the quay. The board noted that silting returned '
        'quickly at the seaward end, and recommended annual surveys.'
    ),
    'R8': (
        'Rag paper dominated European printing until the nineteenth century. Mills pulped old linen and '
        'cotton cloth, and shortages of rags periodically limited how many books could be printed. In the '
        '1840s, Friedrich Keller in Saxony and Charles Fenerty in Nova Scotia independently developed '
        'methods for making paper from wood pulp. Wood-pulp paper was cheaper but contained acids that '
        'made it brittle with age, which is why many books printed between 1850 and 1950 are now crumbling.'
    ),
}

# source key -> ('pp', [chapter.paragraph, ...]) | ('novel', [chapter.scene.paragraph, ...]) | ('research', key)
SOURCES = {
    'A': ('pp', ['0.9']),
    'B': ('pp', ['0.33']),
    'C': ('pp', ['2.0']),
    'D': ('pp', ['2.2']),
    'E': ('pp', ['2.5']),
    'F': ('pp', ['2.6']),
    'G': ('pp', ['2.8', '2.9', '2.10', '2.11', '2.12', '2.13']),
    'H': ('pp', ['3.11', '3.12']),
    'I': ('pp', ['3.13']),
    'J': ('pp', ['4.0']),
    'K': ('pp', ['4.6']),
    'L': ('pp', ['5.11']),
    'M': ('pp', [f'5.{n}' for n in range(25, 36)]),
    'N1': ('novel', ['0.1.0', '0.1.1', '0.1.2']),
    'N2': ('novel', ['1.1.0', '1.1.1', '1.1.2']),
    'N3': ('novel', ['2.0.0', '2.0.1', '2.0.2']),
    'N4': ('novel', ['2.1.0', '2.1.1', '2.1.2']),
    'N5': ('novel', ['3.0.0', '3.0.1', '3.0.2']),
    'N6': ('novel', ['3.1.0', '3.1.1', '3.1.2']),
    'N7': ('novel', ['0.0.0', '0.0.1', '0.0.2']),
    **{key: ('research', key) for key in RESEARCH},
}

S, P, U = 'supported', 'partial', 'unsupported'

# (source, claim, label, kind)
# fmt: off
PAIRS = [
    ('A', 'Netherfield has been taken by a young man of large fortune from the north of England.', S, 'paraphrase'),
    ('A', 'Mrs. Long reports that the new tenant looked over the house on Monday and agreed terms with Mr. Morris at once.', S, 'paraphrase'),
    ('A', 'The new tenant is to take possession before Michaelmas.', S, 'verbatim'),
    ('A', 'Some of his servants are expected to be at Netherfield by the end of the following week.', S, 'paraphrase'),
    ('A', 'The young man took Netherfield on Monday and has already moved in.', P, 'contradicted part'),
    ('A', 'The tenant is a rich young man from the north who will take possession in the spring.', P, 'contradicted part'),
    ('A', 'A young man of large fortune has taken Netherfield, and he is said to be engaged to one of the Lucas daughters.', P, 'extra detail'),
    ('A', 'Mrs. Long told Mrs. Bennet that the new tenant has four or five thousand a year.', P, 'extra detail'),
    ('A', 'The young man who has taken Netherfield is from London.', U, 'contradiction'),
    ('A', 'Mr. Morris refused the young man’s offer for Netherfield.', U, 'contradiction'),
    ('A', 'The new tenant arrived on Friday on horseback.', U, 'contradiction'),
    ('A', 'Mrs. Long has quarrelled with the new tenant.', U, 'absent'),

    ('B', 'Mr. Bennet’s wife had still not come to understand his character after about twenty-three years.', S, 'paraphrase'),
    ('B', 'The main business of Mrs. Bennet’s life was getting her daughters married.', S, 'paraphrase'),
    ('B', 'Mrs. Bennet was a woman of limited understanding and little information.', S, 'paraphrase'),
    ('B', 'Mrs. Bennet liked to visit neighbours and hear the news.', S, 'inference'),
    ('B', 'Mrs. Bennet was a woman of little information, and she spent most of her income on entertaining visitors.', P, 'extra detail'),
    ('B', 'Mr. Bennet was witty and reserved, and he deliberately kept his wife in the dark about his plans.', P, 'extra detail'),
    ('B', 'Mrs. Bennet claimed to be nervous whenever she was discontented, and her physician prescribed rest.', P, 'extra detail'),
    ('B', 'Mrs. Bennet was a woman of sharp intelligence and wide reading.', U, 'contradiction'),
    ('B', 'Mr. Bennet and his wife had been married for only a year.', U, 'contradiction'),
    ('B', 'Mrs. Bennet’s chief solace was her garden.', U, 'absent'),
    ('B', 'Mr. Bennet understood his wife’s character less well than she understood his.', U, 'role swap'),

    ('C', 'Mr. Bennet would not give his family any satisfactory description of Mr. Bingley.', S, 'paraphrase'),
    ('C', 'The Bennet women relied on Lady Lucas’s report for information about Mr. Bingley.', S, 'paraphrase'),
    ('C', 'According to Lady Lucas, Sir William had been delighted with Mr. Bingley.', S, 'paraphrase'),
    ('C', 'Lady Lucas said Mr. Bingley meant to attend the next assembly with a large party.', S, 'paraphrase'),
    ('C', 'Lady Lucas reported that Mr. Bingley was young, handsome and agreeable, and that he had recently inherited a fortune.', P, 'extra detail'),
    ('C', 'Mrs. Bennet and her daughters questioned Mr. Bennet cleverly, and Lady Lucas, who had met Mr. Bingley herself, gave a favourable report.', P, 'extra detail'),
    ('C', 'Lady Lucas’s report was highly favourable, and as a result Mrs. Bennet immediately invited Mr. Bingley to dinner.', P, 'extra detail'),
    ('C', 'Mr. Bennet described Mr. Bingley at length to his daughters.', U, 'contradiction'),
    ('C', 'Lady Lucas reported that Mr. Bingley was proud and disagreeable.', U, 'contradiction'),
    ('C', 'Mr. Bingley had said he did not intend to go to the next assembly.', U, 'contradiction'),
    ('C', 'Sir William Lucas disliked Mr. Bingley.', U, 'contradiction'),

    ('D', 'Mr. Bingley called on Mr. Bennet and stayed about ten minutes in his library.', S, 'paraphrase'),
    ('D', 'Bingley hoped to see the young ladies but met only their father.', S, 'paraphrase'),
    ('D', 'The ladies saw from an upper window that Bingley wore a blue coat and rode a black horse.', S, 'paraphrase'),
    ('D', 'Mr. Bingley returned Mr. Bennet’s visit a few days later and, after ten minutes in the library, was introduced to Jane.', P, 'contradicted part'),
    ('D', 'Bingley paid his call within a few days, riding a black horse, and brought a gift of game for Mrs. Bennet.', P, 'extra detail'),
    ('D', 'Mr. Bingley spent the whole afternoon with the family.', U, 'contradiction'),
    ('D', 'Mr. Bingley was shown into the drawing-room, where he met all five daughters.', U, 'contradiction'),
    ('D', 'Mr. Bennet declined to receive Mr. Bingley.', U, 'contradiction'),

    ('E', 'Mr. Darcy drew the attention of the room by his fine figure and handsome features.', S, 'paraphrase'),
    ('E', 'A report spread through the room within minutes that Darcy had ten thousand a year.', S, 'paraphrase'),
    ('E', 'Mr. Hurst was Mr. Bingley’s brother-in-law, and he merely looked the gentleman.', S, 'paraphrase'),
    ('E', 'The ladies at first thought Darcy handsomer than Bingley, but his manners turned the room against him.', S, 'paraphrase'),
    ('E', 'Darcy was admired for his figure and his income, and his estate in Derbyshire was said to be the finest in England.', P, 'overstatement'),
    ('E', 'Mr. Bingley’s manners were easy and unaffected, and he was the best dancer in the room.', P, 'extra detail'),
    ('E', 'Mr. Darcy was widely liked at the assembly because of his friendly manner.', U, 'contradiction'),
    ('E', 'Darcy’s estate was in Yorkshire.', U, 'contradiction'),
    ('E', 'Bingley’s sisters were plain and badly dressed.', U, 'contradiction'),
    ('E', 'Mr. Hurst was Mr. Darcy’s cousin.', U, 'role swap'),

    ('F', 'Bingley danced every dance and was angry that the ball closed so early.', S, 'paraphrase'),
    ('F', 'Mr. Darcy danced only twice, once with Mrs. Hurst and once with Miss Bingley.', S, 'paraphrase'),
    ('F', 'Mrs. Bennet disliked Darcy especially because he had slighted one of her daughters.', S, 'paraphrase'),
    ('F', 'Bingley spoke of giving a ball himself at Netherfield.', S, 'paraphrase'),
    ('F', 'Darcy danced only with Mrs. Hurst and Miss Bingley, and left the ball early in disgust.', P, 'extra detail'),
    ('F', 'Bingley was lively and unreserved, danced every dance, and promised to hold a ball at Netherfield the following week.', P, 'overstatement'),
    ('F', 'Mr. Darcy danced every dance and was the life of the party.', U, 'contradiction'),
    ('F', 'Mrs. Bennet was delighted with Mr. Darcy’s behaviour.', U, 'contradiction'),
    ('F', 'Bingley left the ball early because he was bored.', U, 'contradiction'),

    ('G', 'Darcy told Bingley that he detested dancing with partners he was not acquainted with.', S, 'paraphrase'),
    ('G', 'Bingley urged Darcy to dance and pointed out one of the Miss Bennets sitting behind him.', S, 'paraphrase'),
    ('G', 'Darcy said that Elizabeth was tolerable but not handsome enough to tempt him.', S, 'paraphrase'),
    ('G', 'Darcy said the only handsome girl in the room was the eldest Miss Bennet.', S, 'inference'),
    ('G', 'Darcy refused to dance and said that Elizabeth was tolerable, adding that he would ask her for the next set if she were not engaged.', P, 'extra detail'),
    ('G', 'Bingley called Jane the most beautiful creature he had ever seen and said Elizabeth was also lively and witty.', P, 'extra detail'),
    ('G', 'Darcy asked Elizabeth to dance but she refused.', U, 'contradiction'),
    ('G', 'Darcy thought Elizabeth the most beautiful woman in the room.', U, 'contradiction'),
    ('G', 'Bingley agreed with Darcy that the girls at the assembly were dull.', U, 'contradiction'),
    ('G', 'Bingley told Darcy he would dance with Elizabeth himself.', U, 'absent'),

    ('H', 'Bingley inherited nearly a hundred thousand pounds from his father.', S, 'paraphrase'),
    ('H', 'Bingley’s father had intended to buy an estate but died before he could.', S, 'paraphrase'),
    ('H', 'Bingley looked over Netherfield for half an hour and took it immediately.', S, 'paraphrase'),
    ('H', 'Bingley held Netherfield as a tenant, not as its owner.', S, 'inference'),
    ('H', 'Bingley inherited a hundred thousand pounds, which he used to buy Netherfield from its owner.', P, 'contradicted part'),
    ('H', 'Miss Bingley was willing to preside at her brother’s table, and she hoped he would marry Miss Darcy.', P, 'extra detail'),
    ('H', 'Bingley bought Netherfield outright after inheriting his father’s property.', U, 'contradiction'),
    ('H', 'Bingley spent several weeks deliberating before taking Netherfield.', U, 'contradiction'),
    ('H', 'Mrs. Hurst had married a very wealthy man.', U, 'contradiction'),

    ('I', 'Bingley and Darcy were close friends despite very different characters.', S, 'paraphrase'),
    ('I', 'Darcy was the cleverer of the two.', S, 'paraphrase'),
    ('I', 'Bingley was readily liked wherever he went, while Darcy often gave offence.', S, 'paraphrase'),
    ('I', 'Darcy was clever and haughty, and his greater wealth made Bingley defer to him.', P, 'extra detail'),
    ('I', 'Bingley admired Darcy’s judgment, and Darcy in turn relied on Bingley’s business sense.', P, 'extra detail'),
    ('I', 'Darcy was warmer and more sociable than Bingley.', U, 'role swap'),
    ('I', 'Bingley was deficient in understanding.', U, 'contradiction'),
    ('I', 'Darcy and Bingley had known each other since they were at school together.', U, 'absent'),

    ('J', 'Sir William Lucas had once been in trade in Meryton and was knighted for an address to the king while he was mayor.', S, 'paraphrase'),
    ('J', 'After his knighthood Sir William left his business and moved to a house about a mile from Meryton, called Lucas Lodge.', S, 'paraphrase'),
    ('J', 'Sir William was friendly and obliging to everyone, even though he was pleased with his rank.', S, 'paraphrase'),
    ('J', 'Sir William was knighted as mayor and kept his shop in Meryton, where he continued to trade.', P, 'contradicted part'),
    ('J', 'Sir William moved to Lucas Lodge, a house a mile from Meryton, because he wanted to be nearer to London.', P, 'extra detail'),
    ('J', 'Sir William’s knighthood made him proud and haughty toward his neighbours.', U, 'contradiction'),
    ('J', 'Sir William inherited his title from his father.', U, 'contradiction'),
    ('J', 'Lucas Lodge stood in the middle of Meryton.', U, 'contradiction'),

    ('K', 'Charlotte overheard Mr. Robinson ask Bingley which woman in the room he thought the prettiest.', S, 'paraphrase'),
    ('K', 'Bingley answered at once that the eldest Miss Bennet was the prettiest, beyond a doubt.', S, 'paraphrase'),
    ('K', 'Bingley told Mr. Robinson that Jane Bennet was the prettiest woman present, and that he intended to ask her to marry him.', P, 'extra detail'),
    ('K', 'Mr. Robinson asked Bingley how he liked the Meryton assemblies, and Bingley replied that he found them dull.', P, 'contradicted part'),
    ('K', 'Bingley named Charlotte Lucas as the prettiest woman in the room.', U, 'contradiction'),
    ('K', 'Mr. Darcy was the one who asked Bingley his opinion of the ladies.', U, 'role swap'),

    ('L', 'Darcy at first hardly allowed that Elizabeth was pretty.', S, 'paraphrase'),
    ('L', 'Darcy came to find Elizabeth’s face uncommonly intelligent because of her dark eyes.', S, 'paraphrase'),
    ('L', 'Elizabeth was unaware that Darcy was becoming interested in her.', S, 'paraphrase'),
    ('L', 'Darcy was caught by Elizabeth’s easy playfulness although he had said her manners were not those of the fashionable world.', S, 'paraphrase'),
    ('L', 'Darcy came to admire Elizabeth’s eyes and figure, and he told Bingley so that same week.', P, 'extra detail'),
    ('L', 'Elizabeth noticed Jane’s growing attachment to Bingley, and she was equally aware of Darcy’s interest in herself.', P, 'contradicted part'),
    ('L', 'Darcy found Elizabeth’s figure heavy and ungraceful.', U, 'contradiction'),
    ('L', 'Elizabeth believed Darcy to be a man who made himself agreeable everywhere.', U, 'contradiction'),

    ('M', 'Darcy replied that he never danced at St. James’s.', S, 'paraphrase'),
    ('M', 'Darcy remarked that even savages can dance.', S, 'paraphrase'),
    ('M', 'Sir William called dancing one of the first refinements of polished society.', S, 'paraphrase'),
    ('M', 'Darcy told Sir William he never danced at St. James’s, because he disliked the crowds there.', P, 'extra detail'),
    ('M', 'Sir William asked whether Darcy had a house in town, and Darcy bowed and invited him to visit.', P, 'extra detail'),
    ('M', 'Darcy said he often danced at St. James’s.', U, 'contradiction'),
    ('M', 'Sir William told Darcy he thought dancing a vulgar amusement.', U, 'contradiction'),
    ('M', 'Darcy asked Sir William to introduce him to Elizabeth.', U, 'absent'),

    ('N1', 'Tom Vane opened the tower door only a little way when Mara came to it.', S, 'paraphrase'),
    ('N1', 'Tom would not explain why the lamp had gone dark.', S, 'paraphrase'),
    ('N1', 'A brass key hung on a nail inside the door.', S, 'paraphrase'),
    ('N1', 'Mara concluded in her notebook that Tom feared something other than the sea.', S, 'paraphrase'),
    ('N1', 'Tom Vane refused to say why the lamp was dark, and he told Mara to leave the island on the next ferry.', P, 'extra detail'),
    ('N1', 'A green-edged brass key hung inside the door, and Tom took it down and hid it when he saw Mara looking.', P, 'contradicted part'),
    ('N1', 'Tom Vane invited Mara inside and showed her the lamp.', U, 'contradiction'),
    ('N1', 'Mara wrote that Tom was afraid of the sea.', U, 'contradiction'),
    ('N1', 'Tom Vane gave Mara the brass key.', U, 'absent'),

    ('N2', 'Mara found a bundle of letters from her father in the desk.', S, 'paraphrase'),
    ('N2', 'The letters were written the winter before her father drowned.', S, 'paraphrase'),
    ('N2', 'One of the letters asked Tom to keep the key safe until the daughter arrived.', S, 'paraphrase'),
    ('N2', 'Mara left the letters as she found them and told nobody about them.', S, 'paraphrase'),
    ('N2', 'Mara found letters from her father in the desk and took them with her to read at the inn.', P, 'contradicted part'),
    ('N2', 'One letter was signed only “Edwin”, and it warned Tom about the lamp.', P, 'extra detail'),
    ('N2', 'The letters were written by Tom Vane to Mara’s mother.', U, 'contradiction'),
    ('N2', 'Mara showed the letters to Bram Hollis at once.', U, 'contradiction'),
    ('N2', 'Mara’s father died in a fire.', U, 'contradiction'),

    ('N3', 'The storm arrived on the third day and the ferry stayed in port.', S, 'paraphrase'),
    ('N3', 'Tom told Mara he had lost the brass key years before, though it hung on the nail behind him.', S, 'paraphrase'),
    ('N3', 'Mara did not accuse Tom of lying; she asked him to teach her to light the lamp by hand.', S, 'paraphrase'),
    ('N3', 'Tom said the key was lost and Mara angrily accused him of lying, then demanded to be taught the lamp.', P, 'contradicted part'),
    ('N3', 'The storm broke over the harbour wall, and Mara asked Tom about the key and the letters.', P, 'extra detail'),
    ('N3', 'The storm struck on the first night and the ferry kept running.', U, 'contradiction'),
    ('N3', 'Tom handed Mara the key and told her the truth.', U, 'contradiction'),

    ('N4', 'A fishing boat hit the rocks below the tower near dawn.', S, 'paraphrase'),
    ('N4', 'Bram rowed out with two men from the inn.', S, 'verbatim'),
    ('N4', 'The light reached the water in time because Mara held the lamp steady while Tom turned the lens.', S, 'paraphrase'),
    ('N4', 'One survivor, a young woman wrapped in a sail, was brought ashore.', S, 'paraphrase'),
    ('N4', 'Bram rowed out with two men from the inn and rescued the whole crew of the fishing boat.', P, 'overstatement'),
    ('N4', 'The fishing boat struck the rocks at midnight, and Mara steadied the lamp while Tom turned the lens.', P, 'contradicted part'),
    ('N4', 'Tom rowed out to the fishing boat while Mara kept the lamp.', U, 'role swap'),
    ('N4', 'No one survived the wreck.', U, 'contradiction'),

    ('N5', 'The survivor gave her name as Ada Lindqvist and said she had come to find the keeper.', S, 'paraphrase'),
    ('N5', 'Ada was carrying a letter in the same handwriting as the bundle in the desk.', S, 'paraphrase'),
    ('N5', 'Tom finally told Mara the truth about her father.', S, 'paraphrase'),
    ('N5', 'Ada Lindqvist said she had come to find the keeper, and she was Mara’s half-sister.', P, 'extra detail'),
    ('N5', 'Mara read Ada’s letter twice before she understood it, then confronted Bram.', P, 'extra detail'),
    ('N5', 'Ada Lindqvist was the daughter of the keeper.', U, 'absent'),
    ('N5', 'Tom refused to say anything about Mara’s father.', U, 'contradiction'),

    ('N6', 'Mara decided to stay at Gull Point through the winter.', S, 'paraphrase'),
    ('N6', 'Bram told her the inn would keep her room.', S, 'paraphrase'),
    ('N6', 'Mara lit the lamp herself that evening, and the brass key was returned to its nail.', S, 'paraphrase'),
    ('N6', 'The storm had passed, and Mara decided to stay for the winter, taking over the keeper’s cottage.', P, 'extra detail'),
    ('N6', 'Mara left on the morning ferry.', U, 'contradiction'),
    ('N6', 'The harbour was rough and grey after the storm.', U, 'contradiction'),

    ('N7', 'Mara arrived at Gull Point by the evening ferry.', S, 'paraphrase'),
    ('N7', 'Mara had not seen Gull Point since she was a child.', S, 'verbatim'),
    ('N7', 'The innkeeper’s hands shook when he mentioned the lamp.', S, 'verbatim'),
    ('N7', 'Bram Hollis served Mara tea and warned her the keeper would not see visitors, adding that the lamp had been dark for a month.', P, 'extra detail'),
    ('N7', 'Nobody on the ferry spoke, and Mara was eager to see the lighthouse again.', P, 'contradicted part'),
    ('N7', 'Mara was travelling to Gull Point for the first time.', U, 'contradiction'),
    ('N7', 'The lighthouse was blazing against the cliffs as the ferry approached.', U, 'contradiction'),

    ('R1', 'Fresnel proposed a lens made of concentric rings of glass prisms in 1822.', S, 'paraphrase'),
    ('R1', 'The first Fresnel lens was installed at the Cordouan lighthouse in 1823.', S, 'verbatim'),
    ('R1', 'Early lighthouse braziers produced a weak light, and smoke dimmed it in damp weather.', S, 'paraphrase'),
    ('R1', 'A first-order lens was the largest class and could be seen from over twenty miles in clear weather.', S, 'paraphrase'),
    ('R1', 'Fresnel’s lens was installed at Cordouan in 1823 and made lighthouses visible from fifty miles away.', P, 'contradicted part'),
    ('R1', 'Fresnel lenses were graded into orders by size, and the largest were used on lighthouses on exposed coasts.', P, 'extra detail'),
    ('R1', 'Fresnel developed his lens after working as a lighthouse keeper.', U, 'absent'),
    ('R1', 'The first Fresnel lens was installed in Britain in 1851.', U, 'contradiction'),
    ('R1', 'Open braziers were more reliable than Fresnel lenses in damp weather.', U, 'contradiction'),

    ('R2', 'Observers counted 412 occupied nests in the first year and 301 in the third.', S, 'paraphrase'),
    ('R2', 'Chick survival was lowest in the third year.', S, 'paraphrase'),
    ('R2', 'The authors say the survey cannot show whether the rat caused the decline.', S, 'paraphrase'),
    ('R2', 'Most nests were on bare ground above the high-tide line.', S, 'paraphrase'),
    ('R2', 'A rat was seen near the northern cliffs in the third year, when chick survival was lowest.', S, 'paraphrase'),
    ('R2', 'Nest counts fell over the three years, and the authors conclude that the rat was responsible.', P, 'contradicted part'),
    ('R2', 'Occupied nests fell from 412 to 301 over three annual surveys, and food supply was found to be adequate.', P, 'contradicted part'),
    ('R2', 'Gull nests increased steadily over the three years.', U, 'contradiction'),
    ('R2', 'The survey took place in December.', U, 'contradiction'),
    ('R2', 'Most nests were built in the old signal station.', U, 'contradiction'),

    ('R3', 'A 1340 catalogue of the Saint-Aure library listed about nine hundred volumes.', S, 'paraphrase'),
    ('R3', 'Borrowing books required the abbot’s written permission.', S, 'paraphrase'),
    ('R3', 'Brother Anselm noted that damp had ruined the lower shelves on the north wall.', S, 'paraphrase'),
    ('R3', 'Two volumes were recorded as missing in the catalogue.', S, 'paraphrase'),
    ('R3', 'Most of the library’s books were copied in its own scriptorium, and Brother Anselm listed them alphabetically by author.', P, 'contradicted part'),
    ('R3', 'Borrowing required the abbot’s permission, and borrowers paid a fee for each volume.', P, 'extra detail'),
    ('R3', 'The library held about nine thousand volumes.', U, 'contradiction'),
    ('R3', 'Anyone in the village could borrow books freely.', U, 'contradiction'),
    ('R3', 'Brother Anselm built a new library after the damp ruined the shelves.', U, 'absent'),

    ('R4', 'Arabica trees begin to bear fruit about three years after planting.', S, 'paraphrase'),
    ('R4', 'Pickers often make several passes over the same tree because the cherries ripen unevenly.', S, 'paraphrase'),
    ('R4', 'Each coffee cherry usually contains two beans.', S, 'paraphrase'),
    ('R4', 'Arabica prefers altitudes between 1,000 and 2,000 metres with cool nights and steady rain.', S, 'paraphrase'),
    ('R4', 'Arabica yields its largest harvests between years five and twenty and tolerates frost better than other coffee species.', P, 'extra detail'),
    ('R4', 'Coffee cherries are either dried whole or pulped and fermented, and the choice determines how many beans each cherry holds.', P, 'contradicted part'),
    ('R4', 'Arabica grows best near sea level in hot, dry climates.', U, 'contradiction'),
    ('R4', 'Trees yield their first harvest in the first year.', U, 'contradiction'),
    ('R4', 'Coffee cherries ripen all at once.', U, 'contradiction'),

    ('R5', 'Honey bees stay active through winter by clustering and shivering their flight muscles for heat.', S, 'paraphrase'),
    ('R5', 'Bees on the outside of the cluster rotate inward so that none stays in the cold for long.', S, 'paraphrase'),
    ('R5', 'A hive that runs out of stored honey before spring will starve even if the weather is mild.', S, 'paraphrase'),
    ('R5', 'The centre of the winter cluster is kept near 30 degrees Celsius.', S, 'paraphrase'),
    ('R5', 'Honey bees cluster around the queen in winter and feed on stored honey, and the queen stops laying eggs entirely until spring.', P, 'extra detail'),
    ('R5', 'Bees do not hibernate; they warm the cluster by shivering, which is why hives lose weight faster in cold snaps.', P, 'extra detail'),
    ('R5', 'Honey bees hibernate through the winter.', U, 'contradiction'),
    ('R5', 'The cluster stays at the same temperature as the surrounding air.', U, 'contradiction'),
    ('R5', 'Bees migrate south for the winter.', U, 'absent'),

    ('R6', 'Spring tides happen at new moon and full moon.', S, 'paraphrase'),
    ('R6', 'The name spring tide has nothing to do with the season.', S, 'paraphrase'),
    ('R6', 'Neap tides occur at the first and last quarter moons, when the tidal range is smallest.', S, 'paraphrase'),
    ('R6', 'Spring tides bring the highest high tides and the lowest low tides.', S, 'paraphrase'),
    ('R6', 'Spring tides occur twice a lunar month, and they are strongest in March because of the equinox.', P, 'extra detail'),
    ('R6', 'Neap tides come at the quarter moons, when the sun and moon pull at right angles, and they flood coastal marshes.', P, 'extra detail'),
    ('R6', 'Spring tides occur at the quarter moons.', U, 'contradiction'),
    ('R6', 'Spring tides are named for the season in which they are strongest.', U, 'contradiction'),
    ('R6', 'Neap tides produce the greatest tidal range.', U, 'contradiction'),

    ('R7', 'The harbour board approved dredging of the main channel in March 1894.', S, 'paraphrase'),
    ('R7', 'The channel was deepened from 14 to 19 feet at low water.', S, 'paraphrase'),
    ('R7', 'The dredging finished two months late because of winter storms.', S, 'paraphrase'),
    ('R7', 'After the dredging, steamers of up to 1,800 tons could reach the quay.', S, 'paraphrase'),
    ('R7', 'Dredging cost an estimated 14,000 pounds and the board recommended annual surveys, which the town council funded.', P, 'extra detail'),
    ('R7', 'Work began in September 1894 and the channel was deepened to 25 feet.', P, 'contradicted part'),
    ('R7', 'The dredging was completed ahead of schedule.', U, 'contradiction'),
    ('R7', 'Silting at the seaward end was no longer a problem after the work.', U, 'contradiction'),
    ('R7', 'The board approved dredging in 1904.', U, 'contradiction'),

    ('R8', 'Until the nineteenth century European printers mainly used paper made from rags.', S, 'paraphrase'),
    ('R8', 'Keller and Fenerty independently developed methods for making paper from wood pulp in the 1840s.', S, 'paraphrase'),
    ('R8', 'Wood-pulp paper was cheaper, but its acids make it brittle over time.', S, 'paraphrase'),
    ('R8', 'Shortages of rags sometimes limited the number of books that could be printed.', S, 'paraphrase'),
    ('R8', 'Wood-pulp paper was developed in the 1840s and was cheaper than rag paper, and it was also easier to print on with fine type.', P, 'extra detail'),
    ('R8', 'Fenerty developed wood-pulp paper in Nova Scotia, and he became wealthy from its manufacture.', P, 'extra detail'),
    ('R8', 'Wood-pulp paper lasts longer than rag paper.', U, 'contradiction'),
    ('R8', 'Rag paper was introduced in the nineteenth century.', U, 'contradiction'),
    ('R8', 'Keller worked in Nova Scotia.', U, 'role swap'),
]
# fmt: on


def source_text(spec, pp, novel):
    kind, ref = spec
    if kind == 'research':
        return RESEARCH[ref]
    if kind == 'pp':
        paragraphs = [pp['chapters'][int(r.split('.')[0])]['paragraphs'][int(r.split('.')[1])] for r in ref]
        return ' '.join(p.strip() for p in paragraphs) if len(ref) > 1 else paragraphs[0].strip()
    chapters = novel['chapters']
    parts = []
    for r in ref:
        c, s, p = (int(x) for x in r.split('.'))
        parts.append(chapters[c]['scenes'][s]['paragraphs'][p])
    return ' '.join(parts)


def build():
    pp = json.loads(PP.read_text(encoding='utf-8'))
    novel = json.loads(NOVEL.read_text(encoding='utf-8'))
    sources = {
        key: {'origin': spec[0], 'text': source_text(spec, pp, novel)} for key, spec in SOURCES.items()
    }
    pairs = [
        {'id': number, 'source': key, 'claim': claim, 'label': label, 'kind': kind}
        for number, (key, claim, label, kind) in enumerate(PAIRS, 1)
    ]
    counts = {label: sum(1 for p in pairs if p['label'] == label) for label in (S, P, U)}
    return {
        'schema': 1,
        'description': 'Hand-labelled claim/source pairs for the entailment stage (context engine plan 9). '
        'Written and labelled by the project; see scripts/build_entailment_fixture.py for the label definitions.',
        'labels': [S, P, U],
        'counts': counts,
        'sources': sources,
        'pairs': pairs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    text = json.dumps(build(), indent=1, ensure_ascii=False) + '\n'
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding='utf-8') != text:
            print('tests/fixtures/entailment/pairs.json is stale; rerun without --check')
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding='utf-8')
    print(f'wrote {OUT.relative_to(ROOT)}: {len(PAIRS)} pairs', build()['counts'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
