import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, GridSearchCV

from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis
from sklearn.ensemble import AdaBoostClassifier, RandomForestClassifier
from sklearn.gaussian_process import GaussianProcessClassifier
from sklearn.gaussian_process.kernels import RBF
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
#from pyriemann.classification import MDM
#from sklearn.tree import DecisionTreeClassifier

classifiers = [
    KNeighborsClassifier(3),
    SVC(kernel='linear', probability=True),
    GaussianProcessClassifier(1.0 * RBF(1.0), random_state=42), # It doesn't have .coef
    DecisionTreeClassifier(max_depth=5, random_state=42), # It doesn't have .coef
    RandomForestClassifier(max_depth=5, n_estimators=10, max_features=1, random_state=42), # It doesn't have .coef
    MLPClassifier(alpha=1, max_iter=1000, random_state=42), #'MLPClassifier' object has no attribute 'coef_'. Did you mean: 'coefs_'?
    AdaBoostClassifier(algorithm="SAMME", random_state=42),
    GaussianNB(),
    QuadraticDiscriminantAnalysis(),
    LinearDiscriminantAnalysis(),
    LogisticRegression(),
    # MDM() Always nan at the end
]


def data_transform(x, subtract_mean:bool =True, subtract_axis:int =0, transpose: bool =False):
    # Transpose the second and third dimension
    if transpose:
        x = np.transpose(x, (0, 2, 1))

    # Normalize the data: subtract the mean image
    if subtract_mean:
        mean_image = np.mean(x, axis=subtract_axis)
        mean_image = np.expand_dims(mean_image, axis=subtract_axis)
        x -= mean_image
    return x


def train_test_val_split(dataX, dataY, valid_flag: bool = False):
    train_ratio = 0.75
    test_ratio = 0.10

    # train is now 75% of the entire data set
    x_train, x_test, y_train, y_test = train_test_split(dataX, dataY, test_size=1 - train_ratio)

    if valid_flag:
        validation_ratio = 1-train_ratio-test_ratio
        # test is now 10% of the initial data set
        # validation is now 15% of the initial data set
        x_val, x_test, y_val, y_test = train_test_split(x_test, y_test,
                                                        test_size=test_ratio / (test_ratio + validation_ratio))
    else:
        x_val = None
        y_val = None
    return x_train, x_test, x_val, y_train, y_test, y_val

def get_best_classificator_and_test_accuracy(data, labels, estimators, param_grid={}):
    cv = StratifiedKFold(n_splits=4, shuffle=True, random_state=42)
    clf = GridSearchCV(estimator=estimators, param_grid=param_grid, cv=cv) # https://stackoverflow.com/questions/52580023/how-to-get-the-best-estimator-parameters-out-from-pipelined-gridsearch-and-cro
    clf.fit(data, labels)

    acc = clf.best_score_ # Best Test Score
    print("Best Test Score: \n{}\n".format(clf.best_score_))

    if acc <= 0.25:
        acc = np.nan
    return clf.best_estimator_, acc

from sklearn.base import BaseEstimator

class ClfSwitcher(BaseEstimator):
    # https://stackoverflow.com/questions/48507651/multiple-classification-models-in-a-scikit-pipeline-python

    def __init__(
        self,
        estimator = SGDClassifier(),
    ):
        """
        A Custom BaseEstimator that can switch between classifiers.
        :param estimator: sklearn object - The classifier
        """

        self.estimator = estimator


    def fit(self, X, y=None, **kwargs):
        self.estimator.fit(X, y)
        return self


    def predict(self, X, y=None):
        return self.estimator.predict(X)


    def predict_proba(self, X):
        return self.estimator.predict_proba(X)


    def score(self, X, y):
        return self.estimator.score(X, y)

    def coef_(self):
        return self.estimator.coef_