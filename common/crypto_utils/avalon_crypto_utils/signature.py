# Copyright 2019 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Functions to perform hash calculation and signature generation
and verification.
Functions based on Spec 1.0 compatibility.
"""
import json
import logging
import secrets
import avalon_crypto_utils.crypto_utility as crypto_utility
from utility.hex_utils import is_valid_hex_str, byte_array_to_hex_str
from error_code.error_status import SignatureStatus
import config.config as pconfig
from ecdsa import VerifyingKey
from ecdsa.util import sigencode_der, sigdecode_der
import hashlib

logger = logging.getLogger(__name__)
# Number of bytes of encrypted session key to encrypt data
NO_OF_BYTES = 16


class ClientSignature(object):
    """
    Class to perform hash calculation, signature generation and verification
    """

    def __init__(self):
        self.private_key = None
        self.public_key = None
        self.param_pool = ["requesterNonce", "workOrderId", "workerId",
                           "requesterId", "inData"]
        self.tcs_worker = pconfig.read_config_from_toml("tcs_config.toml",
                                                        "WorkerConfig")

# -----------------------------------------------------------------------------
    def __payload_json_check(self, json_data):
        """
        Function to check if mandatory parameters are available as per
        param_pool.
        Parameters:
            - json_data is a work order submit request json as per
              Trusted Compute EEA API 6.1.1 Work Order Request Payload.
        """

        data = json.loads(json_data)
        if 'params' not in data:
            logger.error("ERROR: Worker Order Submit Json does not have " +
                         "the required params")
            return False

        data_params = data['params']
        param_valid = True
        for param in self.param_pool:
            if (param not in data_params):
                # List down all the missing Parameters
                logger.error("ERROR: Worker Order Submit Json does not have " +
                             "the required parameter: %s", param)
                param_valid = False

        if param_valid:
            i_obj = data_params['inData']
            for obj in i_obj:
                if 'data' not in obj or not obj["data"] or 'index' not in obj:
                    logger.error(
                        "ERROR: Worker Order Submit Json does not have " +
                        "the required parameter in InData")
                    param_valid = False

        return param_valid

# -----------------------------------------------------------------------------
    def __encrypt_workorder_indata(
            self, input_json_params, session_key, session_iv,
            worker_encryption_key, data_key=None, data_iv=None):
        """
        Function to encrypt inData of workorder
        Parameters:
            - input_json_params is inData and outData elements within the
              work order request as per Trusted Compute EEA API 6.1.7
              Work Order Data Formats.
            - session_key is a one-time encryption key generated by the
              participant submitting the work order.
            - session_iv is an initialization vector if required by the
              data encryption algorithm (encryptedSessionKey).
              The default is all zeros.
            - data_key is a one time key generated by participant used to
              encrypt work order indata
            - data_iv is an initialization vector used along with data_key.
              Default is all zeros.
        """

        indata_objects = input_json_params['inData']
        input_json_params['inData'] = indata_objects
        logger.info("Encrypting Workorder Data")

        i = 0
        for item in indata_objects:
            data = item['data'].encode('UTF-8')
            e_key = item['encryptedDataEncryptionKey'].encode('UTF-8')

            if (not e_key) or (e_key == "null".encode('UTF-8')):
                enc_data = crypto_utility.encrypt_data(
                    data, session_key, session_iv)
                input_json_params['inData'][i]['data'] = \
                    crypto_utility.byte_array_to_base64(enc_data)
                logger.debug(
                    "encrypted indata - %s",
                    crypto_utility.byte_array_to_base64(enc_data))
            elif e_key == "-".encode('UTF-8'):
                # Skip encryption and just encode workorder data to
                # base64 format.
                input_json_params['inData'][i]['data'] = \
                    crypto_utility.byte_array_to_base64(data)
            else:
                enc_data = crypto_utility.encrypt_data(data, data_key, data_iv)
                input_json_params['inData'][i]['data'] = \
                    crypto_utility.byte_array_to_base64(enc_data)
                logger.debug("encrypted indata - %s",
                             crypto_utility.byte_array_to_base64(enc_data))
            i = i + 1

        logger.debug("Workorder InData after encryption: %s", indata_objects)

# -----------------------------------------------------------------------------
    def __calculate_hash_on_concatenated_string(
            self, input_json_params, nonce):
        """
        Function to calculate a hash value of the string concatenating the
        following values:
        requesterNonce, workOrderId, workerId, workloadId, and requesterId.
        Parameters:
            - input_json_params is a collection of parameters,
              as per Off-Chain Trusted Compute EEA API 6.1.1
              Work Order Request Payload
            - nonce a random string generated by the participant.
        """

        workorder_id = (input_json_params['workOrderId']).encode('UTF-8')
        worker_id = (input_json_params['workerId']).encode('UTF-8')
        workload_id = "".encode('UTF-8')
        if 'workloadId' in input_json_params:
            workload_id = (input_json_params['workloadId']).encode('UTF-8')
        requester_id = (input_json_params['requesterId']).encode('UTF-8')

        concat_string = nonce + workorder_id + worker_id + workload_id + \
            requester_id
        concat_hash = bytes(concat_string)
        # SHA-256 hashing is used
        hash_1 = crypto_utility.compute_message_hash(concat_hash)
        result_hash = crypto_utility.byte_array_to_base64(hash_1)

        return result_hash

# -----------------------------------------------------------------------------
    def calculate_datahash(self, data_objects):
        """
        Function to calculate a hash value of the array concatenating dataHash,
        data, encryptedDataEncryptionKey, iv for each item in the
        inData/outData array
        Parameters:
            - data_objects is each item in inData or outData part of workorder
              request as per Trusted Compute EEA API 6.1.7
              Work Order Data Formats
        """

        hash_str = ""
        # Sort the data items based on index field before calculating data hash
        data_objects.sort(key=lambda x: x['index'])
        for item in data_objects:
            datahash = "".encode('UTF-8')
            e_key = "".encode('UTF-8')
            iv = "".encode('UTF-8')
            if 'dataHash' in item:
                datahash = item['dataHash'].encode('UTF-8')
            data = item['data'].encode('UTF-8')
            if 'encryptedDataEncryptionKey' in item:
                e_key = item['encryptedDataEncryptionKey'].encode('UTF-8')
            if 'iv' in item:
                iv = item['iv'].encode('UTF-8')
            concat_string = datahash + data + e_key + iv
            concat_hash = bytes(concat_string)
            hash = crypto_utility.compute_message_hash(concat_hash)
            hash_str = hash_str + crypto_utility.byte_array_to_base64(hash)

        return hash_str

# -------------------------------------------------------------------------------
    def generate_signature(self, hash, private_key):
        """
        Function to generate signature object
        Parameters:
            - hash is the combined array of all hashes calculated on the
              message
            - private_key is Client private key
        Returns tuple(status, signature)
        """
        try:
            self.private_key = private_key
            self.public_key = \
                crypto_utility.get_verifying_key(private_key)
            signature_res = \
                self.private_key.sign_digest_deterministic(
                                                bytes(hash),
                                                sigencode=sigencode_der)
            signature_base64 = crypto_utility.byte_array_to_base64(
                                                        signature_res)
        except Exception as err:
            logger.error("Exception occurred during signature generation: %s",
                         str(err))
            return False, None

        return True, signature_base64


# -----------------------------------------------------------------------------
    def generate_client_signature(
            self, input_json_str, worker, private_key, session_key,
            session_iv, encrypted_session_key, data_key=None, data_iv=None):
        """
        Function to generate client signature
        Parameters:
            - input_json_str is requester Work Order Request payload in a
              JSON-RPC based format defined 6.1.1 Work Order Request Payload
            - worker is a worker object to store all the common details of
              worker as per Trusted Compute EEA API 8.1
              Common Data for All Worker Types
            - private_key is Client private key
            - session_key is one time session key generated by the participant
              submitting the work order.
            - session_iv is an initialization vector if required by the
              data encryption algorithm (encryptedSessionKey).
              The default is all zeros.
            - data_key is a one time key generated by participant used to
              encrypt work order indata
            - data_iv is an initialization vector used along with data_key.
              Default is all zeros.
            - encrypted_session_key is a encrypted version of session_key.
        Returns a tuple containing signature and status
        """

        if (self.__payload_json_check(input_json_str) is False):
            logger.error("ERROR: Signing the request failed")
            return input_json_str, SignatureStatus.FAILED

        if (self.tcs_worker['HashingAlgorithm'] != worker.hashing_algorithm):
            logger.error(
                "ERROR: Signing the request failed. Hashing " +
                "algorithm is not supported for %s", worker.hashing_algorithm)
            return input_json_str, SignatureStatus.FAILED

        if (self.tcs_worker['SigningAlgorithm'] != worker.signing_algorithm):
            logger.error(
                "ERROR: Signing the request failed. Signing " +
                "algorithm is not supported for %s", worker.signing_algorithm)
            return input_json_str, SignatureStatus.FAILED

        input_json = json.loads(input_json_str)
        input_json_params = input_json['params']
        input_json_params["sessionKeyIv"] = byte_array_to_hex_str(session_iv)

        encrypted_session_key_str = byte_array_to_hex_str(
            encrypted_session_key)
        self.__encrypt_workorder_indata(
            input_json_params, session_key,
            session_iv, worker.encryption_key, data_key, data_iv)

        if "requesterNonce" in input_json_params:
            if len(input_json_params["requesterNonce"]) == 0:
                # [NO_OF_BYTES] 16 BYTES for nonce.
                # This is the recommendation by NIST to
                # avoid collisions by the "Birthday Paradox".
                input_json_params["requesterNonce"] = secrets.token_hex(
                    NO_OF_BYTES)
            elif not is_valid_hex_str(input_json_params["requesterNonce"]):
                logger.error("Invalid data format for requesterNonce")
                return input_json_params, SignatureStatus.FAILED
        else:
            logger.error("Missing parameter requesterNonce")
            return input_json_params, SignatureStatus.FAILED

        hash_string_1 = self.__calculate_hash_on_concatenated_string(
            input_json_params, input_json_params["requesterNonce"].encode(
                'UTF-8'
            ))
        data_objects = input_json_params['inData']
        hash_string_2 = self.calculate_datahash(data_objects)

        hash_string_3 = ""
        if 'outData' in input_json_params:
            data_objects = input_json_params['outData']
            hash_string_3 = self.calculate_datahash(data_objects)

        concat_string = hash_string_1 + hash_string_2 + hash_string_3
        concat_hash = bytes(concat_string, 'UTF-8')
        final_hash = crypto_utility.compute_message_hash(concat_hash)

        encrypted_request_hash = crypto_utility.encrypt_data(
            final_hash, session_key, session_iv)
        encrypted_request_hash_str = \
            byte_array_to_hex_str(encrypted_request_hash)
        logger.debug("encrypted request hash: \n%s",
                     encrypted_request_hash_str)

        # Update the input json params
        input_json_params["encryptedRequestHash"] = encrypted_request_hash_str
        status, signature = self.generate_signature(final_hash, private_key)
        if status is False:
            return input_json_str, SignatureStatus.FAILED
        input_json_params['requesterSignature'] = signature
        input_json_params["encryptedSessionKey"] = encrypted_session_key_str
        # Temporary mechanism to share client's public key. Not a part of Spec
        input_json_params['verifyingKey'] = self.public_key
        input_json['params'] = input_json_params
        input_json_str = json.dumps(input_json)
        logger.info("Request Json successfully Signed")

        return input_json_str, SignatureStatus.PASSED

    def _verify_wo_response_signature(self, wo_response,
                                      wo_res_verification_key):
        """
        Function to verify the work order response signature
        Parameters:
            @param wo_response - dictionary contains work order response
            as per Trusted Compute EEA API 6.1.2 Work Order Result Payload
            @param wo_res_verification_key - ECDSA/SECP256K1 public key
            used to verify work order response signature.
        Returns enum type SignatureStatus
        """
        worker_nonce = (wo_response["workerNonce"]).encode('UTF-8')
        signature = wo_response['workerSignature']
        hash_string_1 = self.__calculate_hash_on_concatenated_string(
            wo_response, worker_nonce)
        data_objects = wo_response['outData']
        hash_string_2 = self.calculate_datahash(data_objects)
        concat_string = hash_string_1 + hash_string_2
        concat_hash = bytes(concat_string, 'UTF-8')
        final_hash = crypto_utility.compute_message_hash(concat_hash)
        try:
            _verifying_key = VerifyingKey.from_pem(wo_res_verification_key)
        except Exception as error:
            logger.error("Error in verification key of "
                         "work order response : %s",
                         error)
            return SignatureStatus.INVALID_VERIFICATION_KEY
        decoded_signature = crypto_utility.base64_to_byte_array(signature)
        try:
            sig_result = _verifying_key.verify_digest(decoded_signature,
                                                      bytes(final_hash),
                                                      sigdecode=sigdecode_der)
            if sig_result:
                return SignatureStatus.PASSED
        except Exception as er:
            if("Malformed formatting of signature" in str(er)):
                return SignatureStatus.INVALID_SIGNATURE_FORMAT
            return SignatureStatus.FAILED

    def _verify_wo_verification_key_signature(self,
                                              wo_response,
                                              wo_verification_key,
                                              requester_nonce):
        """
        Function to verify the work order response signature
        Parameters:
            @param wo_response - dictionary contains work order response
            as per Trusted Compute EEA API 6.1.2 Work Order Result Payload
            @param wo_verification_key - ECDSA/SECP256K1 public key used
            to verify work order verification key signature.
            @param requester_nonce - requester generated nonce passed in work
            order request. Required in 2 step verification.
        Returns enum type SignatureStatus
        """
        if requester_nonce is None:
            logger.error("Missing requester_nonce argument")
            return SignatureStatus.FAILED

        concat_string = wo_response["extVerificationKey"] + requester_nonce
        v_key_sig = wo_response["extVerificationKeySignature"]
        v_key_hash = crypto_utility.compute_message_hash(
            bytes(concat_string, 'UTF-8'))
        try:
            _verifying_key = VerifyingKey.from_pem(wo_verification_key)
        except Exception as error:
            logger.error("Error in verification key of"
                         "verification key signature : %s", error)
            return SignatureStatus.INVALID_VERIFICATION_KEY
        decoded_v_key_sig = crypto_utility.base64_to_byte_array(v_key_sig)
        try:
            sig_result = _verifying_key.verify_digest(
                decoded_v_key_sig,
                v_key_hash,
                sigdecode=sigdecode_der)
            if sig_result:
                return SignatureStatus.PASSED
        except Exception as er:
            if("Malformed formatting of signature" in str(er)):
                return SignatureStatus.INVALID_SIGNATURE_FORMAT
            return SignatureStatus.FAILED

# -----------------------------------------------------------------------------
    def verify_signature(self, wo_response, wo_res_verification_key,
                         requester_nonce=None):
        """
        Function to verify the signature received from the enclave
        Parameters:
            @param wo_response - dictionary contains work order response
            as per Trusted Compute EEA API 6.1.2 Work Order Result Payload
            @param wo_res_verification_key - worker ECDSA/SECP256K1
            public key used to verify work order response signature.
            @param requester_nonce - requester generated nonce passed in work
            order request. Required in 2 step verification.
        Returns enum type SignatureStatus
        """
        # if verification_key present in work order response
        # then do 2 step verification
        # step1 - The verification key signature from the
        # response is verified using worker’s public verification key
        # (aka KME's verification key)
        if "extVerificationKey" in wo_response:
            status = self._verify_wo_verification_key_signature(
                wo_response,
                wo_res_verification_key,
                requester_nonce)
            if status == SignatureStatus.PASSED:
                # step2 : work order response signature is verified
                # using the verification key in the response
                return self._verify_wo_response_signature(
                    wo_response,
                    wo_response["extVerificationKey"])
            else:
                return status
        else:
            # In case of singleton worker, it is 1 step
            # verification. Verify work order response signature
            # using singleton worker public verification key.
            return self._verify_wo_response_signature(
                wo_response,
                wo_res_verification_key)

# -----------------------------------------------------------------------------
    def verify_update_receipt_signature(self, input_json):
        """
        Function to verify the signature of work order receipt update
        Parameters:
            - input_json is dictionary contains payload returned by the
              WorkOrderReceiptUpdateRetrieve API as define EEA spec 7.2.7
        Returns enum type SignatureStatus
        """
        input_json_params = input_json

        concat_string = input_json_params["workOrderId"] + \
            str(input_json_params["updateType"]) + \
            input_json_params["updateData"]
        concat_hash = bytes(concat_string, 'UTF-8')
        final_hash = crypto_utility.compute_message_hash(concat_hash)
        signature = input_json_params["updateSignature"]
        verification_key = \
            input_json_params["receiptVerificationKey"].encode("ascii")

        try:
            _verifying_key = VerifyingKey.from_pem(verification_key)
        except Exception as error:
            logger.info("Error in verification key : %s", error)
            return SignatureStatus.INVALID_VERIFICATION_KEY

        decoded_signature = crypto_utility.base64_to_byte_array(signature)
        try:
            sig_result = _verifying_key.verify_digest(decoded_signature,
                                                      bytes(final_hash),
                                                      sigdecode=sigdecode_der)
            if sig_result:
                return SignatureStatus.PASSED
        except Exception as er:
            if("Malformed formatting of signature" in str(er)):
                return SignatureStatus.INVALID_SIGNATURE_FORMAT
            return SignatureStatus.FAILED

# -----------------------------------------------------------------------------
    def verify_create_receipt_signature(self, input_json):
        """
        Function to verify the signature of work order receipt create
        Parameters:
            - input_json is dictionary contains request payload of
              WorkOrderReceiptRetrieve API as define EEA spec 7.2.2
        Returns enum type SignatureStatus
        """
        input_json_params = input_json['params']

        concat_string = input_json_params["workOrderId"] + \
            input_json_params["workerServiceId"] + \
            input_json_params["workerId"] + \
            input_json_params["requesterId"] + \
            str(input_json_params["receiptCreateStatus"]) + \
            input_json_params["workOrderRequestHash"] + \
            input_json_params["requesterGeneratedNonce"]
        concat_hash = bytes(concat_string, "UTF-8")
        final_hash = bytes(crypto_utility.compute_message_hash(concat_hash))
        signature = input_json_params["requesterSignature"]
        verification_key = \
            input_json_params["receiptVerificationKey"].encode("ascii")
        try:
            _verifying_key = VerifyingKey.from_pem(verification_key)
        except Exception as error:
            logger.info("Error in verification key : %s", error)
            return SignatureStatus.INVALID_VERIFICATION_KEY

        decoded_signature = crypto_utility.base64_to_byte_array(signature)
        try:
            sig_result = _verifying_key.verify_digest(decoded_signature,
                                                      final_hash,
                                                      sigdecode=sigdecode_der)
            if sig_result:
                return SignatureStatus.PASSED
        except Exception as er:
            if("Malformed formatting of signature" in str(er)):
                return SignatureStatus.INVALID_SIGNATURE_FORMAT
            return SignatureStatus.FAILED


# -----------------------------------------------------------------------------
    def calculate_request_hash(self, input_json):
        """
        Function to create the work order request hash
        as defined in EEA spec 6.1.8.1
        Parameters:
            - input_json is dictionary contains work order request payload
              as define EEA spec 6.1.1
        Returns hash of work order request as string
        """
        wo_request_params = input_json["params"]
        concat_string = wo_request_params["requesterNonce"] + \
            wo_request_params["workOrderId"] + \
            wo_request_params["workerId"] + \
            wo_request_params["workloadId"] + \
            wo_request_params["requesterId"]
        concat_bytes = bytes(concat_string, "UTF-8")
        # SHA-256 hashing is used
        hash_1 = crypto_utility.byte_array_to_base64(
            crypto_utility.compute_message_hash(concat_bytes)
        )
        hash_2 = self.calculate_datahash(wo_request_params["inData"])
        hash_3 = ""
        if "outData" in wo_request_params and \
                len(wo_request_params["outData"]) > 0:
            hash_3 = self.calculate_datahash(wo_request_params["outData"])
        concat_hash = hash_1 + hash_2 + hash_3
        concat_hash = bytes(concat_hash, "UTF-8")
        final_hash = crypto_utility.compute_message_hash(concat_hash)
        final_hash_str = crypto_utility.byte_array_to_hex(final_hash)
        return final_hash_str
