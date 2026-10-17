"""Hand-written golden queries (data only; resolved and checked by build_eval_queries.py).

Each entry: type, a natural-language query, and `anchors`: verbatim strings that appear in the passages
that answer it. The builder labels the paragraph (or page) around every occurrence of every anchor and
fails if an anchor is missing or matches more than `max` (default 8) places. A passage that repeats an
anchor, such as the unchanged sentences of the earlier drafts, is labelled too.
"""

NOVEL = [
    # factual_lookup
    (
        'factual_lookup',
        'Who inherits the Bennet estate because it is entailed away from the daughters?',
        ['was entailed, in default of heirs male, on a distant relation'],
    ),
    (
        'factual_lookup',
        'What reasons does Mr. Collins give for wanting to marry?',
        ['My reasons for marrying are, first'],
    ),
    (
        'factual_lookup',
        'What living did Wickham say old Mr. Darcy meant for him?',
        ['the best living in his gift'],
    ),
    (
        'factual_lookup',
        'How does Elizabeth tell Darcy that her youngest sister has run off?',
        ['has eloped; has thrown herself into the power of'],
    ),
    (
        'factual_lookup',
        'What does Darcy say when he first proposes at Hunsford?',
        ['In vain have I struggled'],
    ),
    (
        'factual_lookup',
        'Who is the cousin of Darcy that visits Rosings?',
        ['a Colonel Fitzwilliam, the younger son of his uncle'],
    ),
    (
        'factual_lookup',
        'What income did the neighbourhood say Bingley had?',
        ['of his having ten thousand a year'],
    ),
    (
        'factual_lookup',
        'Where does Elizabeth first catch sight of Pemberley House?',
        ['the eye was instantly caught by Pemberley House'],
    ),
    (
        'factual_lookup',
        'What is the business of Mrs. Bennet’s life?',
        ['The business of her life was to get her daughters married'],
    ),
    (
        'factual_lookup',
        'How does Charlotte justify accepting Mr. Collins?',
        ['I ask only a comfortable home'],
    ),
    (
        'factual_lookup',
        'What is the value of Mr. Bennet’s estate each year?',
        ['an estate of two thousand a year'],
    ),
    # thematic
    (
        'thematic',
        'Elizabeth realises she has been blind and prejudiced about Darcy and Wickham',
        ['How despicably have I acted!'],
    ),
    (
        'thematic',
        'Darcy objects to the Bennet family’s lack of propriety and low connections',
        ['total want of propriety so frequently', 'His sense of her inferiority, of its being a degradation'],
    ),
    (
        'thematic',
        'Mrs. Bennet complains about her nerves while her husband teases her',
        ['compassion on my poor nerves'],
    ),
    (
        'thematic',
        'Wickham makes a charming and handsome first impression',
        ['His appearance was greatly in his favour'],
    ),
    (
        'thematic',
        'Lady Catherine bullies Elizabeth to deny an engagement to her nephew',
        ['I insist on being satisfied', 'I am not to be trifled with'],
    ),
    ('thematic', 'Jane is overjoyed after being proposed to', ['she was the happiest creature in the world']),
    (
        'thematic',
        'Mr. Collins is obsequious about his patroness’s condescension',
        ['affability and condescension'],
    ),
    (
        'thematic',
        'Mr. Bennet blames himself for his neglect of his family',
        ['It has been my own doing, and I ought to feel it'],
    ),
    (
        'thematic',
        'Darcy’s pride at the assembly: he finds Elizabeth merely tolerable',
        ['She is tolerable: but not handsome enough'],
    ),
    (
        'thematic',
        'Elizabeth walks alone through the mud to nurse her sister and the Bingley sisters disapprove',
        ['six inches deep in mud'],
    ),
    (
        'thematic',
        'Mr. Bennet forces his daughter to choose between a husband and her mother',
        ['An unhappy alternative is before you, Elizabeth'],
    ),
    # citation_locating (a claim the writer wants a source for)
    (
        'citation_locating',
        'The novel opens with the claim that a rich bachelor must want a wife',
        ['a single man in possession of a good fortune must be in want of a wife'],
    ),
    (
        'citation_locating',
        'Darcy says Elizabeth is too generous to trifle with his feelings',
        ['You are too generous to trifle with me'],
    ),
    (
        'citation_locating',
        'Elizabeth thinks she has judged badly although she prided herself on her discernment',
        ['I, who have prided myself on my discernment'],
    ),
]

SERIES = [
    (
        'factual_lookup',
        'Who does the King of Bohemia say he is when he visits Baker Street?',
        ['You may address me as the Count Von Kramm'],
    ),
    (
        'factual_lookup',
        'Where did Irene Adler hide the photograph?',
        ['recess behind a sliding panel just above the right bell-pull'],
    ),
    (
        'factual_lookup',
        'What is the curious incident of the dog in the night-time?',
        ['To the curious incident of the dog in the night-time'],
    ),
    ('factual_lookup', 'Who is called the Napoleon of crime?', ['He is the Napoleon of crime, Watson']),
    ('factual_lookup', 'Which snake killed Dr. Roylott?', ['It is a swamp adder!']),
    (
        'factual_lookup',
        'In which story does Watson recognise the Countess of Morcar’s blue carbuncle?',
        ['Not the Countess of Morcar’s blue carbuncle!'],
    ),
    ('factual_lookup', 'What does the ritual ask: whose was it?', ['Whose was it?']),
    (
        'factual_lookup',
        'Which brother of Sherlock Holmes has stronger powers of observation?',
        ['Because my brother Mycroft possesses it in a larger degree than I do'],
    ),
    (
        'factual_lookup',
        'What do the letters K. K. K. do to John Openshaw’s uncle?',
        ['‘K. K. K.!’ he shrieked'],
    ),
    ('factual_lookup', 'How much does the League pay each week?', ['salary of £ 4 a week']),
    (
        'factual_lookup',
        'Who is accused of the murder of Colonel Barclay?',
        ['supposed murder of Colonel Barclay, of the Royal Mallows'],
    ),
    (
        'factual_lookup',
        'Where does Holmes go with Watson on the Swiss hillside?',
        ['the falls of Reichenbach'],
    ),
    ('thematic', 'Holmes disguises himself as a drunken groom', ['a drunken-looking groom']),
    (
        'thematic',
        'An opium den in the east end of London',
        ['made use of an opium den in the farthest east of the City'],
    ),
    (
        'thematic',
        'A stepfather who exploits his stepdaughter',
        ['“Your father,” said Holmes, “your stepfather, surely'],
    ),
    (
        'thematic',
        'A governess is asked to cut her hair and sit in a certain chair',
        ['Or to cut your hair quite short before you come to us'],
    ),
    (
        'thematic',
        'A blackmailer from an old convict ship sends a warning',
        ['The game is up. Hudson has told all. Fly for your life.'],
    ),
    (
        'thematic',
        'A father found dead on the road near a pool and the son is accused',
        ['McCarthy left his house at Hatherley'],
    ),
    (
        'thematic',
        'Strange job offered to red-headed men in a Fleet Street office',
        ['7 Pope’s Court, Fleet Street'],
    ),
    ('thematic', 'Holmes says you see but do not observe', ['You see, but you do not observe']),
    (
        'thematic',
        'Never theorise before you have the facts',
        ['It is a capital mistake to theorise before one has data'],
    ),
    (
        'thematic',
        'Flowers and the goodness of Providence',
        ['Our highest assurance of the goodness of Providence'],
    ),
    (
        'citation_locating',
        'Watson says he is married and has drifted away from Holmes',
        ['My marriage had drifted us away from each other'],
    ),
    ('continuity', 'Mrs. Hudson the Baker Street landlady', ['Mrs. Hudson']),
    (
        'continuity',
        'Watson is married and living away from Baker Street',
        ['We were seated at breakfast one morning, my wife and I'],
    ),
    ('continuity', 'Hugh Boone the beggar who lodges at the opium den', ['Hugh Boone']),
]

HUMANITIES = [
    (
        'factual_lookup',
        'How far did the empire of Rome extend in the second century?',
        ['the empire of Rome comprehended the fairest part of the earth'],
    ),
    (
        'factual_lookup',
        'Which period had the happiest condition of the human race according to Gibbon?',
        ['condition of the human race was most happy and prosperous'],
    ),
    (
        'factual_lookup',
        'What did Diocletian say about the cabbages he planted?',
        ['show Maximian the cabbages which he had planted'],
    ),
    (
        'factual_lookup',
        'How does Gibbon describe Zenobia?',
        ['Zenobia was esteemed the most lovely as well as the most heroic of her sex'],
    ),
    (
        'factual_lookup',
        'What are the five causes of the growth of Christianity?',
        ['IV. The pure and austere morals of the Christians'],
    ),
    (
        'factual_lookup',
        'Which emperor built an artificial port on a coast left destitute by nature?',
        ['Hadrian, who had constructed an artificial port'],
    ),
    (
        'factual_lookup',
        'What did Pertinax do with the luxuries of the palace?',
        ['All the instruments of luxury Pertinax exposed to public auction'],
    ),
    (
        'factual_lookup',
        'What did the edict of Artaxerxes forbid?',
        ['By an edict of Artaxerxes, the exercise of every worship, except'],
    ),
    (
        'thematic',
        'Roman toleration of the many forms of worship in the empire',
        ['The various modes of worship, which prevailed in the Roman world'],
    ),
    (
        'thematic',
        'Frontier legions camped along the great rivers',
        ['the legions were encamped on the banks of the great rivers'],
    ),
    (
        'thematic',
        'The long virtuous reign of the good emperors',
        ['During a long period of forty-three years, their virtuous labors were crowned with success'],
    ),
    (
        'thematic',
        'Persecution and protection of the Jews under Persian rule',
        ['The Jews were held in honor by him'],
    ),
    (
        'thematic',
        'The murdered emperor’s head is shown to the soldiers',
        ['was carried in triumph to the Prætorian camp'],
    ),
    (
        'thematic',
        'The hope of a future life as a reason Christianity spread',
        ['The doctrine of a future life, improved by every additional circumstance'],
    ),
    (
        'thematic',
        'Men are the same in every age',
        ['men were what they still are, and that events took place eighteen centuries ago'],
    ),
    (
        'citation_locating',
        'Gibbon says the empire comprised the best-known and most civilised part of the world',
        ['the fairest part of the earth, and the most civilized portion of mankind'],
    ),
    (
        'citation_locating',
        'The Prætorian guards were under the emperor’s direct command',
        ['The Prætorian guards were under his immediate command'],
    ),
]

WEBCLIPS = [
    ('factual_lookup', 'Who patented the Hansom cab and when?', ['First patented in 1834 by Joseph Hansom']),
    (
        'factual_lookup',
        'Who proposed the adhesive postage stamp?',
        ['Sir Rowland Hill proposed an adhesive stamp'],
    ),
    (
        'factual_lookup',
        'What was the postal rate of the Penny Black?',
        ['flat rate of one penny, regardless of distance'],
    ),
    (
        'factual_lookup',
        'Who designed BLAST and where?',
        [
            'was designed by Eugene Myers, Stephen Altschul, Warren Gish, David J. Lipman and Webb Miller at the NIH'
        ],
    ),
    (
        'factual_lookup',
        'Who developed the Needleman–Wunsch algorithm and when?',
        ['The algorithm was developed by Saul B. Needleman and Christian D. Wunsch and published in 1970'],
    ),
    (
        'factual_lookup',
        'When did Smith and Waterman publish their local alignment algorithm?',
        ['In 1981, Smith and Waterman published their Smith–Waterman algorithm'],
    ),
    (
        'factual_lookup',
        'What is a mudlark?',
        ['A mudlark is someone who scavenges the banks and shores of rivers'],
    ),
    (
        'factual_lookup',
        'Why were Gretna blacksmiths called anvil priests?',
        ['The blacksmiths in Gretna became known as "anvil priests"'],
    ),
    (
        'factual_lookup',
        'Who is the De Bruijn graph named after?',
        ['Although De Bruijn graphs are named after Nicolaas Govert de Bruijn'],
    ),
    (
        'factual_lookup',
        'How is Beau Brummell remembered?',
        ['Brummell was remembered afterwards as the preeminent example of the dandy'],
    ),
    (
        'factual_lookup',
        'When was Joseph Bazalgette knighted and by whom?',
        ['Bazalgette was knighted by Queen Victoria in 1875'],
    ),
    (
        'factual_lookup',
        'How does FASTA compare with BLAST in speed?',
        ['FASTA is slower than BLAST, but provides a much wider range of scoring'],
    ),
    (
        'thematic',
        'The authorities removed the water pump handle after the cholera epidemic',
        ['government officials replaced the Broad Street pump handle'],
    ),
    (
        'thematic',
        'A discreet seaside retreat where the prince enjoyed private liaisons',
        ['the pavilion was a discreet location for the prince to enjoy private liaisons'],
    ),
    (
        'thematic',
        'The leading bloodstock horse auctioneer in Europe today',
        ['Tattersalls is the leading bloodstock auctioneer in Europe'],
    ),
    (
        'thematic',
        'Tuning perplexity parameters affects apparent clusters in embeddings',
        ['a good understanding of the parameters for t-SNE is needed'],
    ),
    (
        'thematic',
        'Searching a sequence database faster by indexing k-mers instead of linear search',
        ['BLAT relies on k-mer indexing the database'],
    ),
    (
        'thematic',
        'Dandy who quarrelled with the Prince Regent',
        ['He was a close friend of the Prince Regent'],
    ),
]

CURATED: dict[str, list[dict]] = {
    name: [
        {'type': kind, 'query': query, 'anchors': anchors, **({'max': 12} if kind == 'continuity' else {})}
        for kind, query, anchors in entries
    ]
    for name, entries in {
        'novel': NOVEL,
        'series': SERIES,
        'humanities': HUMANITIES,
        'webclips': WEBCLIPS,
    }.items()
}
