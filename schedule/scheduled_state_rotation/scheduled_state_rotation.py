from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

import __main__
import argparse
import logging
import os
import sys
import yaml

'''
For scheduled_editor_choices to run, a CMS bot user is required for it to mutate Editor Choices.
'''


def get_updated_state_value(state: str = 'draft') -> str:
    states_waterfall = ['scheduled', 'published', 'draft']

    # update the state value according to the original state, only scheduled and published will not cause an exception
    # if state is not scheduled and published, then an exception happens and we do not update it
    try:
        return states_waterfall[states_waterfall.index(state) + 1]
    except:
        return state


def change_editor_choices(config_graphql: dict):
    cms_graphql_endpoint = config_graphql['apiEndpoint']
    gql_transport = RequestsHTTPTransport(
        url=cms_graphql_endpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
        },
        verify=True,
        retries=3,
    )

    gql_client = Client(
        transport=gql_transport,
        fetch_schema_from_transport=False,
    )

    # Authenticate through GraphQL
    qgl_mutation_authenticate_get_token = '''
    mutation {
    authenticate: authenticateUserWithPassword(email: "%s", password: "%s") {
        token
    }
    }
    '''

    username = config_graphql['username']
    password = config_graphql['password']

    mutation = gql(qgl_mutation_authenticate_get_token %
                   (username, password))

    token = gql_client.execute(mutation)['authenticate']['token']

    print(f'{os.path.basename(__file__)} has authenticated as {username}')

    gql_transport_with_token = RequestsHTTPTransport(
        url=cms_graphql_endpoint,
        use_json=True,
        headers={
            "Content-type": "application/json",
            'Authorization': f'Bearer {token}'
        },
        verify=True,
        retries=3,
    )

    gql_authenticated_client = Client(
        transport=gql_transport_with_token,
        fetch_schema_from_transport=False,
    )

    # To query the EditorChoices in state of published and scheduled, so it can tolling update their state
    qgl_query_editor_choices_to_modify = '''
    {
        allEditorChoices(where: {OR: [{state: published}, {state: scheduled}]}) {
            id
            state
        }
    }
    '''

    query = gql(qgl_query_editor_choices_to_modify)
    editor_choices = gql_authenticated_client.execute(query)[
        'allEditorChoices']

    if len(editor_choices) == 0:
        print('There is nothing to be updated. Exit now...')
        exit(0)

    print(
        f'These editor choices are about to be updated: {editor_choices}')

    # To update EditorChoices, data should be an array of objects containing id and data

    new_data_list = ['{id: "%s", data:{state: %s}}' % (
        editor_choice["id"], get_updated_state_value(editor_choice["state"])) for editor_choice in editor_choices]
    new_data_str = '[' + ','.join(new_data_list) + ']'

    print(
        f'The editor choices is going to be updated as: {new_data_str}')

    qgl_mutate_editor_choices_template = '''
    mutation {
        updateEditorChoices(data: %s) {
            id
            state
        }
    }
    '''

    mutation = gql(qgl_mutate_editor_choices_template % new_data_str)
    updateEditorChoices = gql_authenticated_client.execute(mutation)

    print(f'EditorChoices are updated as:{updateEditorChoices}')

    # Unauthenticate user after finishing updating to protect the user. Cronjobs' unauthentication shouldn't interfere each other.
    qgl_mutate_unauthenticate_user = '''
    mutation {
        unauthenticate: unauthenticateUser {
            success
        }
    }
    '''

    mutation = gql(qgl_mutate_unauthenticate_user)
    unauthenticate = gql_authenticated_client.execute(mutation)[
        'unauthenticate']

    if unauthenticate['success'] == True:
        print(f'{os.path.basename(__file__)} has unauthenticated as {username}')
    else:
        print(f'{username} failed to unauthenticate')


__GRAPHQL_CMS_CONFIG_KEY = 'graphqlCMS'


def main(config_graphql: dict = None):
    ''' Import YouTube Channel program starts here '''
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')

    change_editor_choices(config_graphql)

    # 3. Generate and clean up Posts for k5


logging.basicConfig()

if __name__ == '__main__':
    logger = logging.getLogger(__main__.__file__)
    logger.setLevel('INFO')
    logger.info(f'{__file__} is executing...')
    parser = argparse.ArgumentParser(
        description='Process configuration of importPosts')
    parser.add_argument('-g', '--config-graphql', dest=__GRAPHQL_CMS_CONFIG_KEY,
                        help='graphql config file for importPosts', metavar='FILE', type=str, required=True)

    args = parser.parse_args()

    with open(getattr(args, __GRAPHQL_CMS_CONFIG_KEY), 'r') as stream:
        config_graphql = yaml.safe_load(stream)

    main(config_graphql=config_graphql)

    logger.info('exiting...good bye...')
