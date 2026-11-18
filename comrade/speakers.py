"""People the bot can credit when an author quotes them.

Authors quote opponents, newspapers, and officials as well as comrades. A quoted
passage stays in the bank only when its speaker is on this list, under the
canonical name on the left. The aliases are the forms the texts use.
"""
import re
import unicodedata

SPEAKERS = {
    # Writers in the corpus
    "Karl Marx": ["Marx"],
    "Friedrich Engels": ["Engels", "Frederick Engels"],
    "Karl Marx and Friedrich Engels": ["Marx and Engels", "the Communist Manifesto", "Communist Manifesto"],
    "Vladimir Lenin": ["Lenin", "V. I. Lenin", "Vladimir Ilyich Lenin"],
    "Joseph Stalin": ["Stalin"],
    "Rosa Luxemburg": ["Luxemburg"],
    "Mao Zedong": ["Mao", "Mao Tse-tung", "Chairman Mao"],
    "Ho Chi Minh": ["President Ho Chi Minh", "Uncle Ho"],
    "Fidel Castro": ["Fidel", "Castro"],
    "Che Guevara": ["Che", "Guevara", "Ernesto Guevara", "Ernesto Che Guevara"],
    "Malcolm X": [],
    "Eugene V. Debs": ["Eugene Debs", "Eugene Victor Debs", "Debs"],
    "Angela Davis": [],
    "Assata Shakur": [],
    "Frantz Fanon": ["Fanon"],
    "Huey P. Newton": ["Huey Newton"],
    "Walter Rodney": [],
    "Antonio Gramsci": ["Gramsci"],
    "Peter Kropotkin": ["Kropotkin"],
    "Thomas Sankara": ["Sankara"],
    "Fred Hampton": [],
    "Georgi Dimitrov": ["Dimitrov"],
    "Amílcar Cabral": ["Cabral"],
    "Kwame Nkrumah": ["Nkrumah", "Dr. N'Krumah"],
    "Alexandra Kollontai": ["Kollontai"],
    "Clara Zetkin": ["Zetkin"],
    "Helen Keller": [],
    # Socialists, communists, and anarchists the corpus quotes
    "Salvador Allende": ["President Allende", "Allende"],
    "José Martí": ["Marti", "Jose Marti"],
    "W. E. B. Du Bois": ["W. E. B. DuBois", "W.E.B. Du Bois", "Du Bois", "DuBois"],
    "Claudia Jones": [],
    "Emma Goldman": [],
    "Lucy Parsons": [],
    "Big Bill Haywood": ["Bill Haywood", "William Haywood"],
    "Jean Jaurès": ["Jaures"],
    "C. L. R. James": [],
    "George Jackson": [],
    "Rose Pastor Stokes": [],
    "Nelson Mandela": ["Mandela"],
    "Langston Hughes": [],
    "Mariarosa Dalla Costa": [],
    "Maria Mies": [],
    "Paul Mattick": [],
    "Mother Jones": ["Mary Harris Jones"],
    "Elizabeth Gurley Flynn": [],
    "Joe Hill": [],
    "Paul Robeson": [],
    "Patrice Lumumba": ["Lumumba"],
    "Julius Nyerere": ["Nyerere"],
    "Samora Machel": [],
    "Ben Barka": ["Comrade Ben Barka", "Mehdi Ben Barka"],
    "Pablo Neruda": [],
    "Bertolt Brecht": ["Brecht"],
    "Karl Liebknecht": ["Liebknecht"],
    "August Bebel": ["Bebel"],
    "Paul Lafargue": ["Lafargue"],
    "James Connolly": ["Connolly"],
    "Nadezhda Krupskaya": ["Krupskaya"],
    "Leon Trotsky": ["Trotsky"],
    "Mikhail Bakunin": ["Bakunin"],
    "Albert Einstein": ["Einstein"],
    # Abolitionists and liberation figures
    "Frederick Douglass": ["Douglass"],
    "Sojourner Truth": [],
    "Harriet Tubman": [],
    "John Brown": [],
    "Nat Turner": [],
    "David Walker": [],
    "Ida B. Wells": [],
    "Martin Luther King Jr.": ["Martin Luther King", "Martin Luther King, Jr.", "Dr. King", "King"],
    "Ella Baker": [],
    "Fannie Lou Hamer": [],
    "Stokely Carmichael": ["Kwame Ture"],
    "Bobby Seale": [],
    "Shirley Chisholm": [],
    "Angelina Grimké": ["Angelina Grimke"],
    "Sarah Grimké": ["Sarah Grimke"],
    "Elizabeth Cady Stanton": [],
    "Susan B. Anthony": [],
    "Chief Luther Standing Bear": ["Luther Standing Bear"],
    "Tecumseh": [],
    "Chief Joseph": [],
    "Simón Bolívar": ["Bolivar", "Simon Bolivar"],
    "Toussaint Louverture": ["Toussaint L'Ouverture", "Toussaint"],
    "Henry David Thoreau": ["Thoreau"],
    "Mark Twain": [],
    "Daniel Berrigan": ["Dan Berrigan"],
}


def _fold(name: str) -> str:
    """Lowercase, without accents, titles such as Dr., or punctuation."""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_ = re.sub(r"[’']", "'", ascii_.lower())
    ascii_ = re.sub(r"\b(dr|mr|mrs|comrade|president|professor|prof)\b\.?", "", ascii_)
    return re.sub(r"[^a-z0-9']+", " ", ascii_).strip()


_BY_NAME = {_fold(alias): name for name, aliases in SPEAKERS.items() for alias in [name] + aliases}


def canonical(speaker: str):
    """The canonical name for a quoted speaker, or None if the bot shouldn't credit them."""
    return _BY_NAME.get(_fold(speaker or ""))
