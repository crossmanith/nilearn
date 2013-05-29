# Authors : Vincent Michel (vm.michel@gmail.com)
#           Alexandre Gramfort (alexandre.gramfort@inria.fr)
#           Philippe Gervais (philippe.gervais@inria.fr)
#
# License: simplified BSD

import time
import sys
import numpy as np

from sklearn.externals.joblib import Parallel, delayed, cpu_count
from sklearn.svm import LinearSVC
from sklearn.cross_validation import cross_val_score
from sklearn.base import BaseEstimator
from sklearn import neighbors

from . import masking
from . import utils


def search_light(X, y, estimator, A, score_func=None, cv=None, n_jobs=-1,
                 verbose=0):
    """Function for computing a search_light

    Parameters
    ----------
    X: array-like of shape at least 2D
        data to fit.

    y: array-like
        target variable to predict.

    estimator: estimator object implementing 'fit'
        object to use to fit the data

    A: numpy sparse matrix.
        adjacency matrix. Defines for each sample the neigbhoring samples
        following a given structure of the data.

    score_func: callable, optional
        callable taking as arguments the fitted estimator, the
        test data (X_test) and the test target (y_test) if y is
        not None.

    cv: cross-validation generator, optional
        A cross-validation generator. If None, a 3-fold cross
        validation is used or 3-fold stratified cross-validation
        when y is supplied.

    n_jobs: int, optional
        The number of CPUs to use to do the computation. -1 means
        'all CPUs'.

    verbose: int, optional
        The verbosity level. Defaut is 0

    Returns
    -------
    scores: array-like of shape (number of rows in A)
        search_light scores
    """
    scores = np.zeros(len(A.rows), dtype=float)
    group_iter = GroupIterator(A.shape[0], n_jobs)
    scores = Parallel(n_jobs=n_jobs, verbose=verbose)(
        delayed(_group_iter_search_light)(
            list_i, A.rows[list_i],
            estimator, X, y, A.shape[0], score_func, cv, verbose)
        for list_i in group_iter)
    return np.concatenate(scores)


class GroupIterator(object):
    """Group iterator

    Provides group of features for search_light loop
    that may be used with Parallel.

    Parameters
    ----------
    n_features: int
        Total number of features

    n_jobs: int, optional
        The number of CPUs to use to do the computation. -1 means
        'all CPUs'. Defaut is 1
    """
    def __init__(self, n_features, n_jobs=1):
        self.n_features = n_features
        if n_jobs == -1:
            n_jobs = cpu_count()
        self.n_jobs = n_jobs

    def __iter__(self):
        split = np.array_split(np.arange(self.n_features), self.n_jobs)
        for list_i in split:
            yield list_i


def _group_iter_search_light(list_i, list_rows, estimator, X, y, total,
                             score_func, cv, verbose=0):
    """Function for grouped iterations of search_light

    Parameters
    -----------
    list_i: array of int
        Indices of voxels to be processed by the thread.

    list_rows: array of array of integers
        Indices of adjacency rows corresponding to list_i voxels

    estimator: estimator object implementing 'fit'
        The object to use to fit the data

    X: array-like of shape at least 2D
        The data to fit.

    y: array-like
        The target variable to try to predict.

    total: int
        Total number of voxels

    score_func: callable, optional
        callable taking as arguments the fitted estimator, the
        test data (X_test) and the test target (y_test) if y is
        not None.

    cv: cross-validation generator, optional
        A cross-validation generator. If None, a 3-fold cross
        validation is used or 3-fold stratified cross-validation
        when y is supplied.

    verbose: int, optional
        The verbosity level. Defaut is 0

    Returns
    -------
    par_scores: numpy.ndarray
        precision of each voxel. dtype: float64.
    """
    par_scores = np.zeros(len(list_rows))
    thread_id = (list_i[0] + 1) / len(list_i) + 1
    t0 = time.time()
    for i, row in enumerate(list_rows):
        if list_i[i] not in row:
            row.append(list_i[i])
        par_scores[i] = np.mean(cross_val_score(estimator, X[:, row],
                                                y, score_func=score_func,
                                                cv=cv, n_jobs=1))
        if verbose > 0:
            # One can't print less than each 100 iterations
            step = 11 - min(verbose, 10)
            if (i % step == 0):
                # If there is only one job, progress information is fixed
                if total == len(list_rows):
                    crlf = "\r"
                else:
                    crlf = "\n"
                percent = float(i) / len(list_rows)
                percent = round(percent * 100, 2)
                dt = time.time() - t0
                # We use a max to avoid a division by zero
                remaining = (100. - percent) / max(0.01, percent) * dt
                sys.stderr.write(
                    "Job #%d, processed %d/%d voxels"
                    "(%0.2f%%, %i seconds remaining)%s"
                    % (thread_id, i, len(list_rows), percent, remaining, crlf))
    return par_scores


##############################################################################
### Class for search_light ###################################################
##############################################################################
class SearchLight(BaseEstimator):
    """Class to perform a search_light using an arbitrary type of classifier.

    Parameters
    -----------
    mask: boolean matrix.
        data mask

    process_mask: boolean matrix, optional
        mask of the data that will be processed by searchlight

    radius: float, optional
        radius of the searchlight sphere

    estimator: estimator object implementing 'fit'
        The object to use to fit the data

    n_jobs: int, optional. Default is -1.
        The number of CPUs to use to do the computation. -1 means
        'all CPUs'.

    score_func: callable, optional
        callable taking as arguments the fitted estimator, the
        test data (X_test) and the test target (y_test) if y is
        not None.

    cv: cross-validation generator, optional
        A cross-validation generator. If None, a 3-fold cross
        validation is used or 3-fold stratified cross-validation
        when y is supplied.

    verbose: int, optional
        The verbosity level. Defaut is False

    Notes
    ------
    The searchlight [Kriegeskorte 06] is a widely used approach for the
    study of the fine-grained patterns of information in fMRI analysis.
    Its principle is relatively simple: a small group of neighboring
    features is extracted from the data, and the prediction function is
    instantiated on these features only. The resulting prediction
    accuracy is thus associated with all the features within the group,
    or only with the feature on the center. This yields a map of local
    fine-grained information, that can be used for assessing hypothesis
    on the local spatial layout of the neural code under investigation.

    Nikolaus Kriegeskorte, Rainer Goebel & Peter Bandettini.
    Information-based functional brain mapping.
    Proceedings of the National Academy of Sciences
    of the United States of America,
    vol. 103, no. 10, pages 3863-3868, March 2006
    """

    def __init__(self, mask_img, process_mask_img=None, radius=2.,
                 estimator=LinearSVC(C=1), n_jobs=1, score_func=None, cv=None,
                 verbose=0):
        """
        Parameters
        ==========
        mask_img: niimg
            boolean image giving location of voxels containing usable signals.

        process_mask: niimg, optional
            boolean image giving voxels on which searchlight should be
            computed.

        radius: float, optional
            radius of searchlight ball, in millimeters. Defaults to 2.
        """
        self.mask_img = mask_img
        self.process_mask_img = process_mask_img
        self.radius = radius
        self.estimator = estimator
        self.n_jobs = n_jobs
        self.score_func = score_func
        self.cv = cv
        self.verbose = verbose

    def fit(self, niimgs, y):
        """Fit the searchlight

        niimg: niimg
            4D image.

        y: 1D array-like
            Target variable to predict. Must have exactly as many elements as
            3D images in niimg.

        Attributes
        ----------
        scores_: numpy.ndarray
            search_light scores. Same shape as input parameter
            process_mask_img.
        """
        # Compute world coordinates of all in-mask voxels.
        mask, mask_affine = masking._load_mask_img(self.mask_img)
        mask_coords = np.where(mask != 0)
        mask_coords = np.asarray(mask_coords + (np.ones(len(mask_coords[0]),
                                                        dtype=np.int),))
        mask_coords = np.dot(mask_affine, mask_coords)[:3].T

        # Compute world coordinates of all in-process mask voxels
        if self.process_mask_img is None:
            process_mask = mask
            process_mask_coords = mask_coords
        else:
            process_mask, process_mask_affine = \
                masking._load_mask_img(self.process_mask_img)
            process_mask_coords = np.where(process_mask != 0)
            process_mask_coords = \
                np.asarray(process_mask_coords
                           + (np.ones(len(process_mask_coords[0]),
                                      dtype=np.int),))
            process_mask_coords = np.dot(process_mask_affine,
                                         process_mask_coords)[:3].T

        clf = neighbors.NearestNeighbors(radius=self.radius)
        A = clf.fit(mask_coords).radius_neighbors_graph(process_mask_coords)
        del process_mask_coords, mask_coords
        A = A.tolil()

        # scores is an 1D array of CV scores with length equals to the number
        # of voxels in processing mask (columns in process_mask)
        niimgs = utils.check_niimgs(niimgs)
        X = utils.as_ndarray(niimgs.get_data(), order="C")
        X = X[mask].T
        del niimgs, mask
        scores = search_light(X, y, self.estimator, A,
                              self.score_func, self.cv, self.n_jobs,
                              self.verbose)
        scores_3D = np.zeros(process_mask.shape)
        scores_3D[process_mask] = scores
        self.scores_ = scores_3D
        return self
