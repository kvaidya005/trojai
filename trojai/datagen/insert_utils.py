import time

from trojai.datagen.config import InsertAtRandomLocationConfig

from typing import Callable, Sequence, Any, Tuple, Optional

import numpy as np
from scipy.ndimage import filters

import logging
logger = logging.getLogger(__name__)

# all possible directions to leave pixel along, for edge_tracing algorithm
DIRECTIONS = [(-1, -1), (-1, 1), (1, 1), (1, -1), (0, -1), (-1, 0), (0, 1), (1, 0)]


def pattern_fit(chan_img: np.ndarray, chan_pattern: np.ndarray, chan_location: Sequence[Any]) -> bool:
    """
    Returns True if the pattern at the desired location can fit into the image channel without wrap, and False otherwise

    :param chan_img: a numpy.ndarray of shape (nrows, ncols) which represents an image channel
    :param chan_pattern: a numpy.ndarray of shape (prows, pcols) which represents a channel of the pattern
    :param chan_location: a Sequence of length 2, which contains the x/y coordinate of the top left corner of the
            pattern to be inserted for this specific channel
    :return: True/False depending on whether the pattern will fit into the image
    """

    p_rows, p_cols = chan_pattern.shape
    r, c = chan_location
    i_rows, i_cols = chan_img.shape

    if (r + p_rows) > i_rows or (c + p_cols) > i_cols:
        return False

    if not valid_location(chan_img, chan_pattern, chan_location):
        return False

    return True


def valid_location(chan_img: np.ndarray, chan_pattern: np.ndarray, chan_location: Sequence[Any]) -> bool:
    """
    Returns False if the pattern intersects with the given image for top-left corner location

    :param chan_img: a numpy.ndarray of shape (nrows, ncols) which represents an image channel
    :param chan_pattern: a numpy.ndarray of shape (prows, pcols) which represents a channel of the pattern
    :param chan_location: a Sequence of length 2, which contains the x/y coordinate of the top left corner of the
            pattern to be inserted for this specific channel
    :return: True/False depending on whether the location is valid for the given image and pattern
    """

    p_rows, p_cols = chan_pattern.shape
    r, c = chan_location

    if np.logical_or.reduce(chan_img[r:r + p_rows, c:c + p_cols], axis=None):
        return False

    return True


def _get_edge_length_in_direction(curr_i: int, curr_j: int, dir_i: int, dir_j: int, i_rows: int, i_cols: int,
                                  edge_pixels: set) -> int:
    """
    find the maximum length of a move in the given direction along the perimeter of the image
    :param curr_i: current row index
    :param curr_j: current col index
    :param dir_i: direction of change in row index
    :param dir_j: direction of change in col index
    :param i_rows: number of rows of containing array
    :param i_cols number of cols of containing array
    :param edge_pixels: set of remaining edge pixels to visit
    :return: the length of the edge in the given direction, 0 if none exists,
    if direction is a diagonal length will always be <= 1
    """
    length = 0
    while 0 <= curr_i + dir_i < i_rows and 0 <= curr_j + dir_j < i_cols and \
            (curr_i + dir_i, curr_j + dir_j) in edge_pixels:
        # update seen edge pixels
        edge_pixels.remove((curr_i + dir_i, curr_j + dir_j))
        length += 1
        curr_i += dir_i
        curr_j += dir_j
        # only allow length 1 diagonal moves
        if dir_i != 0 and dir_j != 0:
            break
    return length


def _get_next_edge_from_pixel(curr_i: int, curr_j: int, i_rows: int, i_cols: int,
                              edge_pixels: set) -> Optional[Tuple[int, int, int, int]]:
    """
    Obtain the next edge to trace along
    :param curr_i: current row index
    :param curr_j: current col index
    :param i_rows: number of rows of containing array
    :param i_cols: number of cols of containing array
    :param edge_pixels: set of remaining edge pixels to visit
    :return: a tuple of row endpoint, col endpoint, row distance, col distance if an undiscovered edge is found,
    otherwise None
    """
    for dir_i, dir_j in DIRECTIONS:
        length = _get_edge_length_in_direction(curr_i, curr_j, dir_i, dir_j, i_rows, i_cols, edge_pixels)
        if length != 0:
            move_i, move_j = dir_i * length, dir_j * length
            return move_i, move_j
    return None


def _get_bounding_box(img: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """

    :param img:
    :return:
    """
    rows = np.logical_or.reduce(img, axis=1)
    cols = np.logical_or.reduce(img, axis=0)

    row_bounds = np.nonzero(rows)
    col_bounds = np.nonzero(cols)

    if row_bounds[0].size != 0 and col_bounds[0].size != 0:
        top = row_bounds[0][0]
        bottom = row_bounds[0][row_bounds[0].size - 1]

        left = col_bounds[0][0]
        right = col_bounds[0][col_bounds[0].size - 1]

        return left, top, right - left + 1, bottom - top + 1
    else:
        return None


def valid_locations(img: np.ndarray, pattern: np.ndarray, algo_config: InsertAtRandomLocationConfig,
                    protect_wrap: bool = True, allow_overlap: bool = False) -> np.ndarray:
    """
    Returns a list of locations per channel which the pattern can be inserted
    into the img_channel with an overlap algorithm dicated by the appropriate
    inputs

    :param img: a numpy.ndarray which represents the image of shape:
           (nrows, ncols, nchans)
    :param pattern: the pattern to be inserted into the image of shape:
           (prows, pcols, nchans)
    :param protect_wrap: if True, ensures that pattern to be inserted can fit
           without wrapping and raises an Exception otherwise
    :param allow_overlap: if True, then valid locations include locations which
           would overlap any existing images
    :param algo_config: The provided configuration object specifying the algorithm to use and necessary parameters
    :return: A boolean mask of the same shape as the input image, with True
             indicating that that pixel is a valid location for placement of
             the specified pattern
    """
    num_chans = img.shape[2]

    # broadcast the allow_overlap variable if necessary
    if isinstance(allow_overlap, bool):
        allow_overlap = [allow_overlap] * num_chans

    if pattern.shape[2] != num_chans:
        # force user to broadcast the pattern as necessary
        msg = "The # of channels in the pattern does not match the # of channels in the image!"
        logger.error(msg)
        raise ValueError(msg)

    # TODO: look for vectorization opportunities
    output_mask = np.zeros(img.shape, dtype=bool)
    for chan_idx in range(num_chans):
        chan_img = img[:, :, chan_idx]
        chan_pattern = pattern[:, :, chan_idx]
        i_rows, i_cols = chan_img.shape
        p_rows, p_cols = chan_pattern.shape

        if allow_overlap[chan_idx]:
            output_mask[0:i_rows - p_rows + 1,
                        0:i_cols - p_cols + 1,
                        chan_idx] = True
        else:
            if protect_wrap:
                min_val = None
                if isinstance(algo_config.min_val, (int, float)):
                    min_val = algo_config.min_val
                elif len(algo_config.min_val) == num_chans:
                    min_val = algo_config.min_val[chan_idx]
                else:
                    msg = "Size of min_val tuple does not correspond with the number of channels in the image!"
                    logger.error(msg)
                    raise ValueError(msg)

                mask = (chan_img <= min_val)

                # True if image present, False if not
                img_mask = np.logical_not(mask)

                # remove boundaries from valid locations
                mask[i_rows - p_rows + 1:i_rows, :] = False
                mask[:, i_cols - p_cols + 1:i_cols] = False

                # get all edge pixels
                edge_pixel_coords = np.nonzero(
                                        np.logical_and(
                                            np.logical_xor(
                                                filters.maximum_filter(img_mask, 3, mode='constant', cval=0.0),
                                                filters.minimum_filter(img_mask, 3, mode='constant', cval=0.0)),
                                            img_mask))
                edge_pixels = zip(edge_pixel_coords[0], edge_pixel_coords[1])

                if algo_config.algorithm == 'edge_tracing':
                    logger.info("Computing valid locations according to edge_tracing algorithm")

                    edge_pixel_set = set(edge_pixels)
                    # search until all edges have been visited
                    while len(edge_pixel_set) != 0:
                        start_i, start_j = edge_pixel_set.pop()

                        # invalidate relevant pixels for start square
                        top_boundary = max(0, start_i - p_rows + 1)
                        left_boundary = max(0, start_j - p_cols + 1)
                        mask[top_boundary:start_i + 1,
                             left_boundary: start_j + 1] = False

                        curr_i, curr_j = start_i, start_j
                        move = 0, 0
                        while move is not None:
                            # where you are, what vector you took to get there
                            action_i, action_j = move
                            curr_i += action_i
                            curr_j += action_j

                            # truncate when near top or left boundary
                            top_index = max(0, curr_i - p_rows + 1)
                            left_index = max(0, curr_j - p_cols + 1)

                            # update invalidation based on last move,
                            # if action_i or action_j has absolute value greater than 0, the other must be 0,
                            # i.e diagonal moves of length greater than 1 aren't updated correctly by this
                            if action_i < 0:
                                # update top border
                                mask[top_index:top_index - action_i, left_index:curr_j + 1] = False
                            elif action_i > 0:
                                # update bottom border
                                mask[curr_i - action_i + 1:curr_i + 1, left_index:curr_j + 1] = False

                            if action_j < 0:
                                # update left border
                                mask[top_index:curr_i + 1, left_index:left_index - action_j] = False
                            elif action_j > 0:
                                # update right border
                                mask[top_index:curr_i + 1, curr_j - action_j + 1:curr_j + 1] = False

                            # obtain next pixel to inspect
                            move = _get_next_edge_from_pixel(curr_i, curr_j, i_rows, i_cols, edge_pixel_set)

                elif algo_config.algorithm == 'brute_force':
                    for i, j in edge_pixels:
                        mask[max(0, i - p_rows + 1):i + 1, max(0, j - p_cols + 1):j + 1] = False

                elif algo_config.algorithm == 'threshold':
                    for i, j in edge_pixels:
                        mask[max(0, i - p_rows + 1):i + 1, max(0, j - p_cols + 1):j + 1] = False
                    # enumerate all possible invalid locations
                    mask_coords = np.nonzero(mask)
                    possible_locations = zip(mask_coords[0], mask_coords[1])

                    threshold_val = None
                    if isinstance(algo_config.threshold_val, (int, float)):
                        threshold_val = algo_config.threshold_val
                    elif len(algo_config.threshold_val) == num_chans:
                        threshold_val = algo_config.threshold_val[chan_idx]
                    else:
                        msg = "Size of threshold_val tuple does not correspond with " \
                              "the number of channels in the image!"
                        logger.error(msg)
                        raise ValueError(msg)

                    # if average pixel value in location is below specified value, allow possible trigger overlap
                    for i, j in possible_locations:
                        if np.mean(chan_img[i:i + p_rows, j:j + p_cols]) <= threshold_val:
                            mask[i][j] = True

                elif algo_config.algorithm == 'bounding_boxes':
                    # find bounding rectangles of shape over a num_boxes x num_boxes grid
                    for i in range(algo_config.num_boxes):
                        for j in range(algo_config.num_boxes):
                            left = (j * i_cols) // algo_config.num_boxes
                            top = (i * i_rows) // algo_config.num_boxes
                            right = ((j + 1) * i_cols) // algo_config.num_boxes
                            bottom = ((i + 1) * i_rows) // algo_config.num_boxes

                            coords = _get_bounding_box(img_mask[top:bottom, left:right])
                            if coords is not None:
                                x, y, w, h = coords
                                mask[max(0, top + y - p_rows + 1):top + y + h,
                                     max(0, left + x - p_cols + 1):left + x + w] = False

                output_mask[:, :, chan_idx] = mask

            else:
                msg = "Wrapping for trigger insertion has not been implemented yet!"
                logger.error(msg)
                raise ValueError(msg)

    return output_mask
