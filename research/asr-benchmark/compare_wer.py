import re
import jiwer

reference = """Guten Morgen zusammen. Heute sprechen wir über Machine Learning, genauer gesagt über neuronale Netze.
Ein neuronales Netz besteht aus mehreren Schichten, sogenannten Layers.
Beim Training verwenden wir den Backpropagation-Algorithmus, um die Gewichte anzupassen.
In der letzten Vorlesung hatten wir 25 Teilnehmende, und die Klausuranmeldung endet am 15. Dezember.
Für die Übung brauchen Sie Python und die Bibliothek PyTorch. Haben Sie dazu noch Fragen?"""

small = """Guten Morgen zusammen! Heute sprechen wir über Maschinenlöhnen, genauer gesagt über Neuronalnetze.
Ein Neuronalsnetz bestellt aus mehreren Schichten, so genannten Layers.
Beim Training verwendet wir den Backpropagation-Angreeter und die Gerichte anzupassen.
In der letzten Vorlesung hatten wir 25 Feinnähmende und die Pauso-Anmendung endet am 15. Dezember.
Für die Übung brauchen Sie Beide und die Ölothek Beidausch. Haben Sie dazu noch einen?"""

turbo = """Guten Morgen zusammen. Heute sprechen wir über Machine Learning, genauer gesagt über Neuronalnetze.
Ein Neuronalnetz besteht aus mehreren Schichten, sogenannten Layers.
Beim Training verwenden wir den Backpropagation Algorithmus, um die Gerichte anzupassen.
In der letzten Verlesung hatten wir 25 Vereinnehmende und die Klausuranmeldung endet am 12. Dezember.
Für die Übung brauchen Sie Python und die Bibliothek Python. Haben Sie dazu noch Fragen?"""


def normalize(text):
    text = text.lower()
    text = text.replace("-", " ")             # "Backpropagation-Algorithmus" -> two words
    text = re.sub(r"[^\w\s]", "", text)       # remove punctuation: . , ! ?
    text = re.sub(r"\s+", " ", text).strip()  # remove extra spaces and line breaks
    return text


ref = normalize(reference)

for name, hyp_raw in [("small", small), ("large-v3-turbo", turbo)]:
    hyp = normalize(hyp_raw)
    result = jiwer.process_words(ref, hyp)
    print(f"===== {name} =====")
    print(f"WER: {result.wer:.1%}")
    print(f"CER: {jiwer.cer(ref, hyp):.1%}")
    print(f"Correct: {result.hits}, Substitutions: {result.substitutions}, "
          f"Deletions: {result.deletions}, Insertions: {result.insertions}")
    print(jiwer.visualize_alignment(result))