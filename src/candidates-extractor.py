import itertools
import copy
import os.path
import sys
import json

import stanza
from stanza.pipeline.core import DownloadMethod
from nltk.tokenize import wordpunct_tokenize
from embeddings import GloveEmbedding

from utility import *

# doc for stanza : https://stanfordnlp.github.io/stanza/data_objects#document
# Stopwords downloaded on https://www.ranks.nl/stopwords and manually modified


# -- Initializing the project --

if __name__ == '__main__' and len(sys.argv) >= 2:
    fileName = sys.argv[1]
else:
    fileName = input('Enter the name of the file to process : ')

content = get_file_content(fileName, "inputs")
if(content == -1):
    exit(0)


# -- Initializing the Stanza pipeline and GloVe embedding for further processing --

stanza.download('en', processors='tokenize, lemma, pos')
processingPipeline = stanza.Pipeline('en', processors='tokenize, lemma, pos', download_method=DownloadMethod.REUSE_RESOURCES)

doc = processingPipeline(content)
wordsFront = doc.iter_words()
wordsBack = doc.iter_words()

stopwords = set(line.strip() for line in open('stopwords.txt'))

os.environ['HOME'] = os.path.join('..', 'GloVe')
g = GloveEmbedding('common_crawl_48', d_emb=300, show_progress=True)

# We have corresponding lemmas for each word. We need to initialize a window of 30 lemmas and put them in a hash
# table. Every time we have a match -> verify if we have a match inside

# -- Processing function for each next word --

def process_next_word(currentTerm, currentId, storageTable, matchTable, startBlock, endBlock):
    if currentTerm in storageTable:
        # we have a match ! Let's update the storage table
        storageTable[currentTerm].append(currentId)

        # compute all possible pairs for the new match (A in A B B A) (not optimized)
        newPairs = list(itertools.combinations(storageTable[currentTerm], 2))
        newPairs = [comb for comb in newPairs if currentId in comb]

        # compute all possible pairs of old matches (B in A B B A)
        oldMatches = [(oldTerm, list(itertools.combinations(matchTable[oldTerm], 2))) for oldTerm in matchTable]
        
        # iterate over all pairs for the new match
        for newPair in newPairs:
          # iterate over all old matches
          for oldMatch in oldMatches:
            oldTerm = oldMatch[0]
            # iterate over all pairs from the old match to check if it is inside the new match
            for oldPair in oldMatch[1]:
                if (oldPair[0] > newPair[0] and oldPair[1] < newPair[1]):
                    # found a chiasmus candidate                
                    # we need, for each candidate : 
                    #   - the position in the raw text of the first character of the first word of the block
                    #   - the position in the raw text of the last character of the 5th word coming after the block
                    #       -> currently, we take the subsequent 25 characters
                    #   - the position in the block of the words forming the candidate
                    candidateList.append([[startBlock, endBlock + 25], [[newPair[0], newPair[0] + len(currentTerm)], [oldPair[0], oldPair[0] + len(oldTerm)], [oldPair[1], oldPair[1] + len(oldTerm)], [newPair[1], newPair[1] + len(currentTerm)]]])

        # update the match table
        matchTable[currentTerm] = copy.deepcopy(storageTable[currentTerm])
    else:
        # no match, let's update the storage table
        storageTable[currentTerm] = [currentId]

# -- Creating the general variables --

candidateList = []
lemmaTable = {}
matchTable = {}

# -- Initializing the sliding window over the first 30 characters --

for _ in range(30):
    nextWord = next(wordsFront)
    nextWord = ignore_punctuation_and_stopwords(wordsFront, nextWord, stopwords)
    
    # if we reached the end of the file
    if(nextWord == -1):
        break
        
    process_next_word(nextWord.lemma, nextWord.parent.start_char, lemmaTable, matchTable, 0, nextWord.parent.end_char)

# -- Main part : make the window slide using wordsFront and wordsBack --
#    (Same algorithm but delete info relevant to wordsBack when moving forward)

# candidateList is a list containing lists of the form
#   [[info regarding the block], [info regarding the chiasm in itself]]
# where the first list contains :
#   [the position of the first, the position of the last character of the block]
# and the second list contains :
#   [[startFirstTerm, endFirstTerm], [startSecondTerm, endSecondTerm], ...]
#   each "Term" being a word that is part of the chiasmus candidate

# -- Main part : make the window slide using wordsFront and wordsBack --

# foreach stops when wordsFront ends
for nextWord, oldWord in zip(wordsFront, wordsBack):
    # If we are currently processing a "punctuation or stop word", then we ignore it
    nextWord = ignore_punctuation_and_stopwords(wordsFront, nextWord, stopwords)
    # if we reached the end of the file
    if(nextWord == -1):
        break

    oldWord = ignore_punctuation_and_stopwords(wordsBack, oldWord, stopwords)
    oldLemma = oldWord.lemma
    startBlock = oldWord.parent.start_char

    # Processing the front of the window
    process_next_word(nextWord.lemma, nextWord.parent.start_char, lemmaTable, matchTable, startBlock, nextWord.parent.end_char)
    
    # handle the rear of the window
    # Delete the word exiting the sliding window from lemmaTable
    if(len(lemmaTable[oldLemma]) <= 1):
        del lemmaTable[oldLemma]
    else:
        del lemmaTable[oldLemma][0]
    # Updating matchTable if necessary after this deletion
    if oldLemma in matchTable:
        # delete when only one occurrence is left - not a match anymore
        if(len(matchTable[oldLemma]) <= 2):
            del matchTable[oldLemma]
        else:
            del matchTable[oldLemma][0]

# print('-------\ncandidate list (', len(candidateList), ' candidates):')
# for candidateBlock, candidateTerms in candidateList:
#     print(word_from_positions(candidateBlock, content))
#     for term in candidateTerms:
#         print(word_from_positions(term, content), end = " ")
#     print('\n-----')

fileNameCandidates = os.path.join("..", "annotation", os.path.splitext(os.path.basename(fileName))[0] + "-annotator.jsonl")

# format imposed by the usage of Deccano
# "entities" will contain the positions of the chiasmi terms and "cats" the annotation label
# Setting default label to "False" to speed up the annotation process
candidateJson = {"text" : "", "entities" : [], "cats" : "NotAChiasmus"}
termLetters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

with open(fileNameCandidates, 'w') as fileOut:
    
    for candidateBlock, candidateTerms in candidateList:
        candidateJson["text"] = word_from_positions(candidateBlock, content)
        candidateJson["entities"] = []
        
        for letterIndex, termPair in enumerate(candidateTerms):
            newPair = []
            for term in termPair:
                term = term - candidateBlock[0]
                newPair.append(term)
            if letterIndex < len(candidateTerms)/2:
                newPair.append(termLetters[letterIndex] + "-1")
            else:
                newPair.append((termLetters[len(candidateTerms) - letterIndex - 1]) + "-2")
            candidateJson["entities"].append(newPair)
        
        # adding metadata useful for post-annotation processings
        candidateJson["startBlock"] = candidateBlock[0]
        candidateJson["endBlock"] = candidateBlock[1]

        fileOut.write(json.dumps(candidateJson))
        fileOut.write("\n")
    
    fileOut.close()

print("\n---------")
print("Candidates stored in", fileNameCandidates)