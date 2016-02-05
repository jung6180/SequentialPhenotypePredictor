import os
import sys
lib_path = os.path.abspath(os.path.join('..', 'lib'))
sys.path.append(lib_path)

from icd9 import ICD9
from sklearn import metrics
import csv
import json
from collections import defaultdict
import gensim


class BinaryPredictor(object):

    def __init__(self, filename):
        self._hit = self._miss = 0
        self._uniq_events = set()
        self._diags = set()
        self._filename = filename

        with open(filename) as f:
            lines = f.readlines()
            for line in lines:
                events = line.split("|")[2].split(" ") + line.split("|")[3].\
                    replace("\n", "").split(" ")
                self._uniq_events |= set(events)
                self._diags |= set([x for x in events if x.startswith('d_')])

        self._nevents = len(self._uniq_events)
        self._events_index = sorted(self._uniq_events)
        self._reset_stats()
        self._generate_icd9_lookup()
        self._diags = list(self._diags)

    def _reset_stats(self):
        self._stats = {}
        self._true_vals = {}
        self._pred_vals = {}
        self._total_test = 0
        self._total_predictions = 0
        for diag in self._diags:
            self._stats[diag] = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
            self._true_vals[diag] = []
            self._pred_vals[diag] = []

    def _generate_icd9_lookup(self):
        self._diag_to_desc = {}
        tree = ICD9('../lib/icd9/codes.json')

        for d in self._diags:
            try:
                self._diag_to_desc[d] = tree.find(d[2:]).description
            except:
                if d[2:] == "285.9":
                    self._diag_to_desc[d] = "Anemia"
                elif d[2:] == "287.5":
                    self._diag_to_desc[d] = "Thrombocytopenia"
                elif d[2:] == "285.1":
                    self._diag_to_desc[d] = "Acute posthemorrhagic anemia"
                else:
                    self._diag_to_desc[d] = "Not Found"

    def base_train(self, filename, skipgram=0):
        '''
        This function trains the sequences on word2vec exculding stopwords and calculates prior
        probabilities. These 2 functions jumbled into one for efficiency.
        '''
        self._filename = filename
        self._prior = {}
        self._model = None

        diag_totals = defaultdict(lambda: 0)
        diag_joined = defaultdict(lambda: 0)
        sentences = []
        self.seq_count = 0

        with open(filename) as f:
            for s in f:
                self.seq_count += 1
                sentences.append(s.split("|")[2].split(" ") +
                                 s.split("|")[3].replace("\n", "").split(" "))
                next_diags = s.split("|")[0].split(",")
                prev_diags = [e for e in s.split("|")[2].split(" ") if e.startswith("d_")]
                for d in prev_diags:
                    diag_totals[d] += 1
                    if d in next_diags:
                        diag_joined[d] += 1

        for d in diag_totals:
            self._prior[d] = diag_joined[d] * 1.0 / diag_totals[d]

        if self._stopwords != 0:
            sentences = self._remove_stopwords(sentences)

        self._model = gensim.models.Word2Vec(sentences, sg=skipgram, window=self._window,
                                             size=self._size, min_count=1, workers=20)

    def _remove_stopwords(self, sentences):
        '''
        This function has shown over and over again that it is not useful
        '''
        self._word_counter = defaultdict(lambda: 0)
        for sentence in sentences:
            for word in sentence:
                self._word_counter[word] += 1

        inverse = {v: k for k, v in self._word_counter.items()}
        topwords = sorted(inverse.keys(), reverse=True)[:self._stopwords]
        self._stopwordslist = [inverse[k] for k in topwords]

        newsentences = []
        for s in sentences:
            newsentences.append([w for w in s if w not in self._stopwordslist])

        return newsentences

    def stat_prediction(self, prediction, actual, diag, prior=None):
        if prior is not None:
            prediction *= abs((self._prior[diag] - int(not prior)))
        prob = (prediction > self._threshold)
        true_condition = (actual == 1)

        if prob:
            self._total_predictions += 1

        self._true_vals[diag].append(actual)
        self._pred_vals[diag].append(prediction)

        if prob is True and true_condition is True:
            self._stats[diag]["TP"] += 1
            self._hit += 1
        elif prob is False and true_condition is True:
            self._miss += 1
            self._stats[diag]["FN"] += 1
        elif prob is True and true_condition is False:
            self._miss += 1
            self._stats[diag]["FP"] += 1
        elif prob is False and true_condition is False:
            self._hit += 1
            self._stats[diag]["TN"] += 1
        else:
            assert False, "This shouldnt happen"

    def cross_validate(self, train_files, test_files):
        self._reset_stats()
        for i, train in enumerate(train_files):
            self.train(train_files[i])
            self.test(test_files[i])

    @property
    def prediction_per_patient(self):
        return (1.0 * self._total_predictions / (self._miss + self._hit))

    @property
    def accuracy(self):
        return (1.0 * self._hit / (self._miss + self._hit))

    @property
    def csv_name(self):
        fname = self.__class__.__name__
        for k in sorted(self._props):
            fname += "_" + k[:2] + str(self._props[k])
        fname += ".csv"
        return fname

    def report_accuracy(self):
        with open('../Results/accuracies.csv', 'a') as csvfile:
            writer = csv.writer(csvfile)
            props = {k: self._props[k] for k in self._props}
            props["model"] = self.__class__.__name__
            writer.writerow([self.accuracy, json.dumps(props, sort_keys=True),
                             self.prediction_per_patient])

    def write_stats(self):
        with open('../Results/Stats/' + self.csv_name, 'w') as csvfile:
            writer = csv.writer(csvfile)
            header = ["Diagnosis", "Description", "AUC", "F-Score", "Specificity", "Sensitivity",
                      "Accuracy", "True Positives", "True Negatives", "False Positives",
                      "False Negatives"]
            writer.writerow(header)
            for d in sorted(self._diags):
                row = []
                row.append(d)
                row.append(self._diag_to_desc[d])
                row.append(self._d_auc(d))
                row.append(self._d_fscore(d))
                row.append(self._d_specificity(d))
                row.append(self._d_sensitivity(d))
                row.append(self._d_accuracy(d))
                row.append(self._stats[d]["TP"])
                row.append(self._stats[d]["TN"])
                row.append(self._stats[d]["FP"])
                row.append(self._stats[d]["FN"])
                writer.writerow(row)

    def _d_auc(self, d):
        return (metrics.roc_auc_score(self._true_vals[d], self._pred_vals[d]))

    def _d_specificity(self, d):
        if self._stats[d]["TP"] + self._stats[d]["FN"] == 0:
            return (self._stats[d]["TP"] / 1.0)
        else:
            return (self._stats[d]["TP"]*1.0 / (self._stats[d]["TP"] + self._stats[d]["FN"]))

    def _d_sensitivity(self, d):
        if self._stats[d]["FP"] + self._stats[d]["TN"] == 0:
            return (self._stats[d]["TN"] / 1.0)
        else:
            return (self._stats[d]["TN"]*1.0 / (self._stats[d]["FP"] + self._stats[d]["TN"]))

    def _d_accuracy(self, d):
        return (self._stats[d]["TN"]*1.0 + self._stats[d]["TP"]) / sum(self._stats[d].values())*1.0

    def _d_precision(self, d):
        if self._stats[d]["FP"] + self._stats[d]["TP"] == 0:
            return (self._stats[d]["TP"] / 1.0)
        else:
            return (self._stats[d]["TP"]*1.0 / (self._stats[d]["TP"] + self._stats[d]["FP"]))

    def _d_fscore(self, d):
        return ((2 * self._d_precision(d) * self._d_sensitivity(d)) /
                (self._d_precision(d) + self._d_sensitivity(d)))